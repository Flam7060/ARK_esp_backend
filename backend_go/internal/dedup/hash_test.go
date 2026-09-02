package dedup

import (
	"testing"

	"ark_relay/internal/protocol"
)

func TestContentHash_SameInputsSameHash(t *testing.T) {
	a := ContentHash(protocol.CategoryStructure, "MetalWall_C", 100.0, 200.0, 300.0)
	b := ContentHash(protocol.CategoryStructure, "MetalWall_C", 100.0, 200.0, 300.0)
	if a != b {
		t.Fatalf("expected identical hash for identical inputs, got %q vs %q", a, b)
	}
}

func TestContentHash_SameGridCellSameHash(t *testing.T) {
	// Both coordinates round to the same 300-unit grid cell.
	a := ContentHash(protocol.CategoryStructure, "MetalWall_C", 100.0, 200.0, 300.0)
	b := ContentHash(protocol.CategoryStructure, "MetalWall_C", 110.0, 190.0, 305.0)
	if a != b {
		t.Fatalf("expected same-cell coordinates to hash identically, got %q vs %q", a, b)
	}
}

func TestContentHash_DifferentClassDifferentHash(t *testing.T) {
	a := ContentHash(protocol.CategoryStructure, "MetalWall_C", 100.0, 200.0, 300.0)
	b := ContentHash(protocol.CategoryStructure, "WoodWall_C", 100.0, 200.0, 300.0)
	if a == b {
		t.Fatalf("expected different class to change the hash, both were %q", a)
	}
}

func TestContentHash_DifferentGridCellDifferentHash(t *testing.T) {
	a := ContentHash(protocol.CategoryStructure, "MetalWall_C", 0.0, 0.0, 0.0)
	b := ContentHash(protocol.CategoryStructure, "MetalWall_C", 1000.0, 0.0, 0.0)
	if a == b {
		t.Fatalf("expected a different grid cell to change the hash, both were %q", a)
	}
}

func TestKey_RoundTripsWithHashFromKeyAndCategoryFromKey(t *testing.T) {
	key := Key(protocol.CategoryTurret, "AutoTurret_C", 10.0, 20.0, 30.0)

	cat, ok := CategoryFromKey(key)
	if !ok || cat != protocol.CategoryTurret {
		t.Fatalf("CategoryFromKey(%q) = %q, %v; want %q, true", key, cat, ok, protocol.CategoryTurret)
	}

	hash, ok := HashFromKey(key)
	if !ok {
		t.Fatalf("HashFromKey(%q) failed", key)
	}
	want := ContentHash(protocol.CategoryTurret, "AutoTurret_C", 10.0, 20.0, 30.0)
	if hash != want {
		t.Fatalf("HashFromKey(%q) = %q, want %q", key, hash, want)
	}
}

func TestCategoryFromKey_RejectsMalformed(t *testing.T) {
	if _, ok := CategoryFromKey(""); ok {
		t.Fatal("expected empty key to fail")
	}
	if _, ok := CategoryFromKey("no-colon-here"); ok {
		t.Fatal("expected key without a colon to fail")
	}
	if _, ok := CategoryFromKey(":leading-colon"); ok {
		t.Fatal("expected key with empty category prefix to fail")
	}
}

func TestHashFromKey_RejectsMalformed(t *testing.T) {
	if _, ok := HashFromKey("structure:"); ok {
		t.Fatal("expected key with empty hash suffix to fail")
	}
	if _, ok := HashFromKey("no-colon-here"); ok {
		t.Fatal("expected key without a colon to fail")
	}
}
