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

// The bug KeyForDino exists to fix: the old client-supplied
// "dino:{label}:{team}" shape gave every identically-named animal of a team
// one shared key, so a server's wild population collapsed into a single
// live-view entry. Distinct positions must produce distinct keys.
func TestKeyForDino_DifferentPositionsDifferentKeys(t *testing.T) {
	a := KeyForDino("Rex_Character_BP_C", 0, 100.0, 200.0, 300.0)
	b := KeyForDino("Rex_Character_BP_C", 0, 100_000.0, 200.0, 300.0)
	if a == b {
		t.Fatalf("expected dinos far apart to get distinct keys, both were %q", a)
	}
}

func TestKeyForDino_DifferentTeamsDifferentKeys(t *testing.T) {
	// A wild rex and a tribe's tamed rex on the same spot are two animals;
	// a density map counting per tribe breaks if they share a key. This is
	// why KeyForDino includes team while ContentHash deliberately doesn't.
	wild := KeyForDino("Rex_Character_BP_C", 0, 100.0, 200.0, 300.0)
	tamed := KeyForDino("Rex_Character_BP_C", 1387, 100.0, 200.0, 300.0)
	if wild == tamed {
		t.Fatalf("expected team to change the dino key, both were %q", wild)
	}
}

func TestKeyForDino_SameCellSameKey(t *testing.T) {
	// Cross-client agreement: the same animal reported by two teammates
	// with slightly different sampled coordinates must count once, not
	// twice. Both coordinates round into the same gridStep cell.
	a := KeyForDino("Rex_Character_BP_C", 1387, 100.0, 200.0, 300.0)
	b := KeyForDino("Rex_Character_BP_C", 1387, 110.0, 190.0, 305.0)
	if a != b {
		t.Fatalf("expected same-cell coordinates to share a key, got %q vs %q", a, b)
	}
}

func TestKeyForDino_CategoryPrefixRecoverable(t *testing.T) {
	// streamVanished routes bare keys by their category prefix, so the
	// rewritten shape has to keep that property.
	cat, ok := CategoryFromKey(KeyForDino("Rex_Character_BP_C", 1387, 1, 2, 3))
	if !ok || cat != protocol.CategoryDino {
		t.Fatalf("expected a recoverable dino prefix, got cat=%q ok=%v", cat, ok)
	}
}
