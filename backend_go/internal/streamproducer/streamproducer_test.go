package streamproducer

import (
	"testing"
	"time"

	"ark_relay/internal/protocol"
)

// Без явного поля backend_python's DinoSighting.tamed (дефолт False)
// отбраковал бы и ручных тоже -- то есть дино перестали бы сохраняться
// вообще. Проверяем, что поле реально уезжает в стрим.
func TestEntityFields_DinoCarriesTamed(t *testing.T) {
	e := protocol.Entity{Cat: protocol.CategoryDino, ClassName: "Rex_Character_BP_C", Tamed: true}
	fields := EntityFields(e, "hash", "1.2.3.4:7777", time.Now(), "acct-1", "")

	if fields["tamed"] != "true" {
		t.Fatalf("expected tamed=true in the dino stream fields, got %q", fields["tamed"])
	}
}

func TestEntityFields_StructureHasNoTamed(t *testing.T) {
	// Владение структурой выражается трайбом; "приручено" для неё пустой
	// признак, и класть его значило бы гнать шум в каждый XADD.
	e := protocol.Entity{Cat: protocol.CategoryStructure, ClassName: "MetalWall_C"}
	fields := EntityFields(e, "hash", "1.2.3.4:7777", time.Now(), "acct-1", "")

	if _, ok := fields["tamed"]; ok {
		t.Fatalf("expected no tamed field for a structure, got fields=%v", fields)
	}
}

func TestPlayerFields_PrefersSteamIDOverStableID(t *testing.T) {
	e := protocol.Entity{StableID: 555, SteamID: 76561198335594996, Label: "Steve"}
	fields := PlayerFields(e, "1.2.3.4:7777", time.Now(), "acct-1")

	if fields["platform_id"] != "76561198335594996" {
		t.Fatalf("expected platform_id to prefer SteamID, got %q", fields["platform_id"])
	}
}

func TestPlayerFields_FallsBackToStableIDWithoutSteamID(t *testing.T) {
	// SteamID unset -- the target's owner may have disconnected between
	// capture and send (read_player_steam_id returns 0 once PlayerState is
	// gone), or the client build predates the SteamID chain entirely.
	e := protocol.Entity{StableID: 555, Label: "Steve"}
	fields := PlayerFields(e, "1.2.3.4:7777", time.Now(), "acct-1")

	if fields["platform_id"] != "555" {
		t.Fatalf("expected platform_id to fall back to StableID, got %q", fields["platform_id"])
	}
}
