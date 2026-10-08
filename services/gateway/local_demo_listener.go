package main

import (
	"errors"
	"net"
	"os"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"unsafe"
)

const localDemoAck = "local-demo-listener-v1 127.0.0.1:8844\n"

type localDemoFDs struct{ listener, ready int }

// Operator-only inheritance: no request, environment or handler can supply FDs.
// The ordinary entry retains all arguments, including its existing validation.
func localDemoFlags(mode string, args []string) (*localDemoFDs, []string, error) {
	values := map[string]int{}
	rest := make([]string, 0, len(args))
	for i := 0; i < len(args); i++ {
		key := args[i]
		if key != "-local-demo-listener-fd" && key != "-local-demo-ready-fd" {
			if strings.HasPrefix(key, "-local-demo-listener-fd=") || strings.HasPrefix(key, "-local-demo-ready-fd=") {
				return nil, nil, errors.New("invalid local demo descriptors")
			}
			rest = append(rest, key)
			continue
		}
		if _, exists := values[key]; exists || i+1 == len(args) {
			return nil, nil, errors.New("invalid local demo descriptors")
		}
		i++
		raw := args[i]
		if raw == "" || strings.Trim(raw, "0123456789") != "" {
			return nil, nil, errors.New("invalid local demo descriptors")
		}
		value, err := strconv.Atoi(raw)
		if err != nil || value < 3 {
			return nil, nil, errors.New("invalid local demo descriptors")
		}
		values[key] = value
	}
	if len(values) == 0 {
		return nil, rest, nil
	}
	if mode == "private_machine" || len(values) != 2 || values["-local-demo-listener-fd"] == values["-local-demo-ready-fd"] {
		return nil, nil, errors.New("invalid local demo descriptors")
	}
	return &localDemoFDs{values["-local-demo-listener-fd"], values["-local-demo-ready-fd"]}, rest, nil
}

// Native constants are platform-specific; unknown platforms fail closed.
func demoReusePort() int {
	switch runtime.GOOS {
	case "darwin":
		return 0x0200
	case "linux":
		return 15
	default:
		return -1
	}
}

// This is the exact ROOT-approved Darwin ABI, not a prefix/state-only check.
func validDarwinDemoInfo(info []byte, err error) bool {
	return err == nil && len(info) == 112 && info[0] == 1
}

func darwinDemoInfo(fd int) ([]byte, error) {
	var buffer [256]byte
	length := uint32(len(buffer))
	_, _, errno := syscall.Syscall6(syscall.SYS_GETSOCKOPT, uintptr(fd), syscall.IPPROTO_TCP, 0x106,
		uintptr(unsafe.Pointer(&buffer[0])), uintptr(unsafe.Pointer(&length)), 0)
	runtime.KeepAlive(&buffer)
	if errno != 0 {
		return nil, errno
	}
	if length > uint32(len(buffer)) {
		return nil, errors.New("unknown native listener layout")
	}
	return buffer[:length], nil
}

func nativeDemoListening(fd int) bool {
	switch runtime.GOOS {
	case "darwin":
		info, err := darwinDemoInfo(fd)
		return validDarwinDemoInfo(info, err)
	case "linux":
		value, err := syscall.GetsockoptInt(fd, syscall.SOL_SOCKET, syscall.SO_ACCEPTCONN)
		return err == nil && value == 1
	default:
		return false
	}
}

func adoptLocalDemoListener(fds *localDemoFDs, expectedAddr string) (net.Listener, error) {
	if fds == nil {
		return nil, nil
	}
	bad := errors.New("invalid inherited local demo listener")
	if fds.listener < 3 || fds.ready < 3 || fds.listener == fds.ready {
		if fds.listener >= 3 {
			syscall.Close(fds.listener)
		}
		if fds.ready >= 3 && fds.ready != fds.listener {
			syscall.Close(fds.ready)
		}
		return nil, bad
	}
	// Transfer ownership even on failure. Never open a replacement listener.
	socketFile := os.NewFile(uintptr(fds.listener), "local-demo-listener")
	readyFile := os.NewFile(uintptr(fds.ready), "local-demo-ready")
	keepReady := false
	defer func() {
		if socketFile != nil {
			socketFile.Close()
		}
		if !keepReady && readyFile != nil && fds.ready != fds.listener {
			readyFile.Close()
		}
	}()
	if demoReusePort() < 0 || socketFile == nil || readyFile == nil || fds.listener < 3 || fds.ready < 3 || fds.listener == fds.ready {
		return nil, bad
	}
	var socketStat, readyStat syscall.Stat_t
	if syscall.Fstat(fds.listener, &socketStat) != nil || socketStat.Mode&syscall.S_IFMT != syscall.S_IFSOCK || syscall.Fstat(fds.ready, &readyStat) != nil || readyStat.Mode&syscall.S_IFMT != syscall.S_IFIFO {
		return nil, bad
	}
	flags, _, errno := syscall.Syscall(syscall.SYS_FCNTL, uintptr(fds.ready), syscall.F_GETFL, 0)
	if errno != 0 || flags&syscall.O_ACCMODE != syscall.O_WRONLY {
		return nil, bad
	}
	address, err := syscall.Getsockname(fds.listener)
	ipv4, ok := address.(*syscall.SockaddrInet4)
	if err != nil || !ok || ipv4.Addr != [4]byte{127, 0, 0, 1} || net.JoinHostPort("127.0.0.1", strconv.Itoa(ipv4.Port)) != expectedAddr {
		return nil, bad
	}
	for _, check := range []struct{ option, want int }{{syscall.SO_TYPE, syscall.SOCK_STREAM}, {syscall.SO_REUSEADDR, 0}, {demoReusePort(), 0}} {
		value, err := syscall.GetsockoptInt(fds.listener, syscall.SOL_SOCKET, check.option)
		if err != nil || value != check.want {
			return nil, bad
		}
	}
	if !nativeDemoListening(fds.listener) {
		return nil, bad
	}
	syscall.CloseOnExec(fds.listener)
	syscall.CloseOnExec(fds.ready)
	if syscall.SetNonblock(fds.ready, true) != nil {
		return nil, bad
	}
	listener, err := net.FileListener(socketFile) // duplicate is close-on-exec in net
	if err != nil {
		return nil, bad
	}
	keepReady = true
	return &localDemoListener{Listener: listener, ready: readyFile}, nil
}

type localDemoListener struct {
	net.Listener
	mu     sync.Mutex
	ready  *os.File
	ackErr error
}

func (l *localDemoListener) Accept() (net.Conn, error) {
	l.mu.Lock()
	if l.ready != nil {
		// A <= PIPE_BUF frame written nonblocking cannot hang on a retained/full pipe.
		var n int
		var err error
		for {
			n, err = syscall.Write(int(l.ready.Fd()), []byte(localDemoAck))
			if err != syscall.EINTR {
				break
			}
		}
		l.ready.Close()
		l.ready = nil
		if err != nil || n != len(localDemoAck) {
			l.ackErr = errors.New("local demo acknowledgement unavailable")
		}
	}
	err := l.ackErr
	l.mu.Unlock()
	if err != nil {
		return nil, err
	}
	return l.Listener.Accept()
}

func (l *localDemoListener) Close() error {
	l.mu.Lock()
	if l.ready != nil {
		l.ready.Close()
		l.ready = nil
	}
	l.mu.Unlock()
	return l.Listener.Close()
}
