package apikeycache

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"testing"

	"github.com/redis/go-redis/v9"
)

// fakeGetter answers Get from an in-memory map -- no real Redis needed.
type fakeGetter struct {
	values map[string]string
}

func (f *fakeGetter) Get(_ context.Context, key string) *redis.StringCmd {
	cmd := redis.NewStringCmd(context.Background())
	if value, ok := f.values[key]; ok {
		cmd.SetVal(value)
	} else {
		cmd.SetErr(redis.Nil)
	}
	return cmd
}

func TestResolve_KnownTokenReturnsAccountID(t *testing.T) {
	token := "plaintext-api-key"
	sum := sha256.Sum256([]byte(token))
	digest := hex.EncodeToString(sum[:])

	rdb := &fakeGetter{values: map[string]string{
		"ark:api_key:tok:" + digest: "acct-1",
	}}
	c := New(rdb)

	accountID, err := c.Resolve(context.Background(), token)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if accountID != "acct-1" {
		t.Fatalf("expected acct-1, got %q", accountID)
	}
}

func TestResolve_UnknownTokenReturnsErrNotFound(t *testing.T) {
	c := New(&fakeGetter{values: map[string]string{}})

	_, err := c.Resolve(context.Background(), "never-cached")
	if !errors.Is(err, ErrNotFound) {
		t.Fatalf("expected ErrNotFound, got %v", err)
	}
}
