package main

import (
	"bytes"
	"context"
	"io"
	"net"
	"os"
	"os/exec"
	"runtime"
	"strconv"
	"syscall"
	"testing"
	"time"
)

func TestLocalDemoFlags(t *testing.T) {
	for _, args := range [][]string{
		{"-local-demo-listener-fd", "3"}, {"-local-demo-ready-fd", "4"},
		{"-local-demo-listener-fd", "3", "-local-demo-ready-fd", "3"},
		{"-local-demo-listener-fd", "2", "-local-demo-ready-fd", "4"},
		{"-local-demo-listener-fd", "+3", "-local-demo-ready-fd", "4"},
		{"-local-demo-listener-fd", "3", "-local-demo-listener-fd", "5", "-local-demo-ready-fd", "4"},
		{"-local-demo-listener-fd=3", "-local-demo-ready-fd", "4"},
	} {
		if _, _, err := localDemoFlags("local", args); err == nil {
			t.Fatalf("accepted invalid pair: %q", args)
		}
	}
	args := []string{"-shipment-local-python", "/fixed/python"}
	if f, rest, err := localDemoFlags("private_machine", args); err != nil || f != nil || len(rest) != len(args) {
		t.Fatal("changed ordinary flags")
	}
	pair := []string{"-local-demo-listener-fd", "3", "-local-demo-ready-fd", "4"}
	if _, _, err := localDemoFlags("private_machine", pair); err == nil {
		t.Fatal("private mode accepted pair")
	}
	if f, rest, err := localDemoFlags("local", append(pair, args...)); err != nil || f.listener != 3 || f.ready != 4 || len(rest) != 2 {
		t.Fatal("valid pair rejected")
	}
}

func demoFixture(t *testing.T, reuse bool) (net.Listener, *localDemoFDs, *os.File, string) {
	t.Helper()
	lc := net.ListenConfig{Control: func(_, _ string, c syscall.RawConn) error {
		var setup error
		err := c.Control(func(fd uintptr) {
			value := 0
			if reuse {
				value = 1
			}
			if setup = syscall.SetsockoptInt(int(fd), syscall.SOL_SOCKET, syscall.SO_REUSEADDR, value); setup == nil {
				setup = syscall.SetsockoptInt(int(fd), syscall.SOL_SOCKET, demoReusePort(), value)
			}
		})
		if err != nil {
			return err
		}
		return setup
	}}
	parent, err := lc.Listen(context.Background(), "tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { parent.Close() })
	raw, err := parent.(*net.TCPListener).File()
	if err != nil {
		t.Fatal(err)
	}
	rd, wr, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	socketFD, err := syscall.Dup(int(raw.Fd()))
	if err != nil {
		t.Fatal(err)
	}
	raw.Close()
	readyFD, err := syscall.Dup(int(wr.Fd()))
	if err != nil {
		t.Fatal(err)
	}
	wr.Close()
	t.Cleanup(func() { rd.Close() })
	return parent, &localDemoFDs{socketFD, readyFD}, rd, parent.Addr().String()
}

func TestLocalDemoAdoptionAckAndReservation(t *testing.T) {
	parent, fds, rd, addr := demoFixture(t, false)
	listener, err := adoptLocalDemoListener(fds, addr)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	_, _, errno := syscall.Syscall(syscall.SYS_FCNTL, uintptr(fds.listener), syscall.F_GETFD, 0)
	if errno != syscall.EBADF {
		t.Fatal("raw socket was not closed")
	}
	readyFlags, _, errno := syscall.Syscall(syscall.SYS_FCNTL, uintptr(fds.ready), syscall.F_GETFD, 0)
	if errno != 0 || readyFlags&syscall.FD_CLOEXEC == 0 {
		t.Fatal("ack descriptor leaks through exec")
	}
	conn, err := listener.(*localDemoListener).Listener.(*net.TCPListener).SyscallConn()
	if err != nil {
		t.Fatal(err)
	}
	conn.Control(func(fd uintptr) {
		flags, _, errno := syscall.Syscall(syscall.SYS_FCNTL, fd, syscall.F_GETFD, 0)
		if errno != 0 || flags&syscall.FD_CLOEXEC == 0 {
			t.Error("listener duplicate leaks through exec")
		}
	})
	_ = rd.SetReadDeadline(time.Now().Add(20 * time.Millisecond))
	if _, err := rd.Read(make([]byte, 1)); !os.IsTimeout(err) {
		t.Fatal("premature acknowledgement")
	}
	_ = rd.SetReadDeadline(time.Now().Add(time.Second))
	accepted := make(chan error, 1)
	go func() {
		conn, err := listener.Accept()
		if conn != nil {
			conn.Close()
		}
		accepted <- err
	}()
	body, err := io.ReadAll(rd)
	if err != nil || string(body) != localDemoAck {
		t.Fatal("invalid ownership acknowledgement")
	}
	client, err := net.DialTimeout("tcp4", addr, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	client.Close()
	if err := <-accepted; err != nil {
		t.Fatal(err)
	}
	listener.Close()
	if outsider, err := net.Listen("tcp4", addr); err == nil {
		outsider.Close()
		t.Fatal("reservation lost before parent close")
	}
	parent.Close()
}

func TestLocalDemoRejectsUnsafeDescriptors(t *testing.T) {
	for _, variant := range []string{"reuse", "address", "read-pipe", "same", "socket-as-pipe", "regular-as-socket", "closed"} {
		t.Run(variant, func(t *testing.T) {
			_, fds, rd, addr := demoFixture(t, variant == "reuse")
			if variant == "address" {
				addr = "127.0.0.1:1"
			}
			if variant == "read-pipe" {
				syscall.Close(fds.ready)
				fds.ready, _ = syscall.Dup(int(rd.Fd()))
			}
			if variant == "same" {
				syscall.Close(fds.ready)
				fds.ready = fds.listener
			}
			if variant == "socket-as-pipe" {
				syscall.Close(fds.ready)
				fds.ready, _ = syscall.Dup(fds.listener)
			}
			if variant == "regular-as-socket" {
				syscall.Close(fds.listener)
				f, err := os.CreateTemp(t.TempDir(), "regular")
				if err != nil {
					t.Fatal(err)
				}
				fds.listener, _ = syscall.Dup(int(f.Fd()))
				f.Close()
			}
			if variant == "closed" {
				syscall.Close(fds.listener)
			}
			if listener, err := adoptLocalDemoListener(fds, addr); err == nil {
				listener.Close()
				t.Fatal("unsafe descriptor accepted")
			}
		})
	}
	if listener, err := adoptLocalDemoListener(nil, "irrelevant"); err != nil || listener != nil {
		t.Fatal("ordinary startup changed")
	}
}

func TestLocalDemoDarwinNativeRoles(t *testing.T) {
	if runtime.GOOS != "darwin" {
		t.Fatal("approved Darwin ABI required")
	}
	fd, err := syscall.Socket(syscall.AF_INET, syscall.SOCK_STREAM, syscall.IPPROTO_TCP)
	if err != nil {
		t.Fatal(err)
	}
	defer syscall.Close(fd)
	syscall.CloseOnExec(fd)
	for _, stage := range []string{"unbound", "bound"} {
		if stage == "bound" {
			if err := syscall.Bind(fd, &syscall.SockaddrInet4{Addr: [4]byte{127, 0, 0, 1}}); err != nil {
				t.Fatal(err)
			}
		}
		info, err := darwinDemoInfo(fd)
		if err != nil || len(info) != 112 || info[0] != 0 || nativeDemoListening(fd) {
			t.Fatal("nonlistener role accepted")
		}
	}
	if err := syscall.Listen(fd, 8); err != nil {
		t.Fatal(err)
	}
	info, err := darwinDemoInfo(fd)
	if err != nil || len(info) != 112 || info[0] != 1 || !nativeDemoListening(fd) {
		t.Fatal("native listener rejected")
	}
	address, err := syscall.Getsockname(fd)
	if err != nil {
		t.Fatal(err)
	}
	port := address.(*syscall.SockaddrInet4).Port
	client, err := net.DialTimeout("tcp4", net.JoinHostPort("127.0.0.1", strconv.Itoa(port)), time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	accepted, _, err := syscall.Accept(fd)
	if err != nil {
		t.Fatal(err)
	}
	defer syscall.Close(accepted)
	syscall.CloseOnExec(accepted)
	raw, err := client.(*net.TCPConn).SyscallConn()
	if err != nil {
		t.Fatal(err)
	}
	raw.Control(func(clientFD uintptr) {
		for _, connected := range []int{accepted, int(clientFD)} {
			info, err := darwinDemoInfo(connected)
			if err != nil || len(info) != 112 || info[0] != 4 || nativeDemoListening(connected) {
				t.Error("connected descriptor accepted as listener")
			}
		}
	})
	for _, role := range []struct{ family, kind, protocol int }{{syscall.AF_INET, syscall.SOCK_DGRAM, syscall.IPPROTO_UDP}, {syscall.AF_UNIX, syscall.SOCK_STREAM, 0}} {
		wrong, err := syscall.Socket(role.family, role.kind, role.protocol)
		if err != nil {
			t.Fatal(err)
		}
		if nativeDemoListening(wrong) {
			t.Error("wrong native type accepted")
		}
		syscall.Close(wrong)
	}
	if nativeDemoListening(-1) {
		t.Fatal("invalid descriptor accepted")
	}
}

func TestLocalDemoDarwinMalformedInfo(t *testing.T) {
	for _, sample := range []struct {
		length, state int
		err           error
	}{{0, 0, nil}, {1, 1, nil}, {111, 1, nil}, {113, 1, nil}, {256, 1, nil}, {112, 0, nil}, {112, 4, nil}, {112, 255, nil}, {112, 1, syscall.ENOPROTOOPT}, {112, 1, syscall.EBADF}} {
		info := make([]byte, sample.length)
		if len(info) > 0 {
			info[0] = byte(sample.state)
		}
		if validDarwinDemoInfo(info, sample.err) {
			t.Fatal("malformed/error native payload accepted")
		}
	}
	info := make([]byte, 112)
	info[0] = 1
	if !validDarwinDemoInfo(info, nil) {
		t.Fatal("exact native ABI rejected")
	}
}

func TestLocalDemoAckFailureCannotBlockOrRetry(t *testing.T) {
	for _, scenario := range []string{"closed", "full"} {
		t.Run(scenario, func(t *testing.T) {
			_, fds, rd, addr := demoFixture(t, false)
			listener, err := adoptLocalDemoListener(fds, addr)
			if err != nil {
				t.Fatal(err)
			}
			defer listener.Close()
			if scenario == "closed" {
				rd.Close()
			} else {
				for {
					_, err := syscall.Write(fds.ready, make([]byte, 4096))
					if err == syscall.EAGAIN {
						break
					}
					if err != nil {
						t.Fatal(err)
					}
				}
			}
			done := make(chan error, 1)
			go func() { _, err := listener.Accept(); done <- err }()
			select {
			case err := <-done:
				if err == nil {
					t.Fatal("ack failure accepted")
				}
			case <-time.After(time.Second):
				t.Fatal("ack blocked")
			}
			if _, err := listener.Accept(); err == nil {
				t.Fatal("failed ack retried as success")
			}
		})
	}
}

func TestLocalDemoConcurrentAcceptClose(t *testing.T) {
	_, fds, rd, addr := demoFixture(t, false)
	listener, err := adoptLocalDemoListener(fds, addr)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	done := make(chan error, 2)
	for i := 0; i < 2; i++ {
		go func() {
			conn, err := listener.Accept()
			if conn != nil {
				conn.Close()
			}
			done <- err
		}()
	}
	_ = rd.SetReadDeadline(time.Now().Add(time.Second))
	body, err := io.ReadAll(rd)
	if err != nil || string(body) != "local-demo-listener-v1 127.0.0.1:8844\n" {
		t.Fatal("ack not exactly once")
	}
	listener.Close()
	for i := 0; i < 2; i++ {
		select {
		case <-done:
		case <-time.After(time.Second):
			t.Fatal("accept did not unblock")
		}
	}
}

// Exercise the actual main entry's rejection order, never a serving path.
func TestLocalDemoMainOperatorGuards(t *testing.T) {
	if scenario := os.Getenv("LOCAL_DEMO_TEST_MAIN"); scenario != "" {
		os.Args = []string{os.Args[0], "-local-demo-listener-fd", "3", "-local-demo-ready-fd", "4"}
		main()
		t.Fatal("invalid operator configuration reached serving path")
	}
	for _, scenario := range []string{"private", "wrong-address"} {
		t.Run(scenario, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestLocalDemoMainOperatorGuards$")
			cmd.Env = []string{"PATH=/usr/bin:/bin", "LOCAL_DEMO_TEST_MAIN=" + scenario}
			want := "invalid local demo address"
			if scenario == "private" {
				want = "invalid local demo configuration"
				cmd.Env = append(cmd.Env, "GATEWAY_MODE=private_machine", "PORT=8080", "GATEWAY_AUDIENCE=https://gateway.invalid", "PYTHON_SERVICE_URL=https://python.invalid", "PYTHON_SERVICE_AUDIENCE=https://python.invalid", "DATABASE_URL_FILE=/nonexistent-synthetic-db", "MACHINE_IDENTITIES_FILE=/nonexistent-synthetic-identities", "WEB_ASSETS_DIR=/nonexistent-synthetic-assets")
			} else {
				cmd.Env = append(cmd.Env, "GATEWAY_MODE=local", "GATEWAY_ADDR=127.0.0.1:8855")
			}
			body, err := cmd.CombinedOutput()
			if ctx.Err() != nil || err == nil || !bytes.Contains(body, []byte(want)) {
				t.Fatal("main failed operator rejection order")
			}
		})
	}
}

func TestLocalDemoOperatorParsersPreserveTypedConfiguration(t *testing.T) {
	cfg, addr, err := runtimeFromEnv(func(key string) string {
		return map[string]string{"GATEWAY_MODE": "local", "GATEWAY_ADDR": "127.0.0.1:8844"}[key]
	})
	if err != nil || addr != "127.0.0.1:8844" {
		t.Fatal("local runtime configuration changed")
	}
	args := []string{"-local-demo-listener-fd", "3", "-local-demo-ready-fd", "4", "-shipment-local-contexts", "/synthetic/catalog", "-shipment-local-python", "/synthetic/python", "-shipment-local-source", "/synthetic/source", "-voice-local-python", "/synthetic/voice-python", "-voice-local-source", "/synthetic/voice-source"}
	fds, rest, err := localDemoFlags(cfg.Mode, args)
	if err != nil || fds == nil {
		t.Fatal("demo flags unavailable")
	}
	cfg, rest, err = withShipmentLocalFlags(cfg, rest)
	if err != nil {
		t.Fatal(err)
	}
	cfg, err = withVoiceLocalFlags(cfg, rest)
	if err != nil || cfg.ShipmentContextsPath != "/synthetic/catalog" || cfg.ShipmentPythonPath != "/synthetic/python" || cfg.ShipmentSourcePath != "/synthetic/source" || cfg.VoicePythonPath != "/synthetic/voice-python" || cfg.VoiceSourcePath != "/synthetic/voice-source" {
		t.Fatal("existing operator paths changed")
	}
}
