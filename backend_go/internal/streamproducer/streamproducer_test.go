package streamproducer

import (
	"testing"
	"time"

	"ark_relay/internal/protocol"
)

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
