package main

import (
	"context"
	"errors"

	"cloud.google.com/go/auth/credentials/idtoken"
)

// Production validation uses the vendor's signature- and audience-validating
// SDK. Tests can supply a certificate-only HTTP client to the SDK validator.
type googleMachineVerifier struct{ validator *idtoken.Validator }

func newGoogleVerifier() (machineVerifier, error) {
	validator, err := idtoken.NewValidator(nil)
	if err != nil {
		return nil, err
	}
	return googleMachineVerifier{validator: validator}, nil
}

func (g googleMachineVerifier) Verify(ctx context.Context, raw, audience string) (verifiedIdentity, error) {
	p, err := g.validator.Validate(ctx, raw, audience)
	if err != nil {
		return verifiedIdentity{}, err
	}
	email, _ := p.Claims["email"].(string)
	verified, _ := p.Claims["email_verified"].(bool)
	return verifiedIdentity{
		Issuer: p.Issuer, Audience: p.Audience, Subject: p.Subject,
		Email: email, EmailVerified: verified, IssuedAt: p.IssuedAt, Expires: p.Expires,
	}, nil
}

// tokenProviderAdapter avoids exposing credentials outside the SDK boundary.
type tokenProviderAdapter struct {
	token func(context.Context) (string, error)
}

func (a tokenProviderAdapter) Token(ctx context.Context) (string, error) { return a.token(ctx) }

func newGoogleTokenSource(audience string) (outboundTokenSource, error) {
	if !serviceOrigin(audience, "https") {
		return nil, errors.New("invalid audience")
	}
	creds, err := idtoken.NewCredentials(&idtoken.Options{Audience: audience})
	if err != nil {
		return nil, err
	}
	return tokenProviderAdapter{token: func(ctx context.Context) (string, error) {
		tok, err := creds.Token(ctx)
		if err != nil || tok == nil || tok.Value == "" {
			return "", errors.New("outbound identity unavailable")
		}
		return tok.Value, nil
	}}, nil
}
