package hub

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"sync"
	"testing"
	"time"

	"ark_relay/internal/protocol"
	"ark_relay/internal/store"
)

func testLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

// fakeEntityWriter records every Upsert call; never fails.
type fakeEntityWriter struct {
	mu    sync.Mutex
	calls []store.EntityWrite
}

func (f *fakeEntityWriter) Upsert(_ context.Context, w store.EntityWrite) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, w)
	return nil
}

func (f *fakeEntityWriter) count() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.calls)
}

// fakeHashCache is an in-memory stand-in for store.EntityStore's
// LastHash/SetLastHash pair.
type fakeHashCache struct {
	mu    sync.Mutex
	hash  map[string]string
	at    map[string]time.Time
	fails bool
}

func newFakeHashCache() *fakeHashCache {
	return &fakeHashCache{hash: map[string]string{}, at: map[string]time.Time{}}
}

func (f *fakeHashCache) key(groupID, serverIP, key string) string {
	return groupID + "|" + serverIP + "|" + key
}

func (f *fakeHashCache) LastHash(_ context.Context, groupID, serverIP, key string) (string, time.Time, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	k := f.key(groupID, serverIP, key)
	return f.hash[k], f.at[k], nil
}

func (f *fakeHashCache) SetLastHash(_ context.Context, groupID, serverIP, key, hash string, at time.Time) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	k := f.key(groupID, serverIP, key)
	f.hash[k] = hash
	f.at[k] = at
	return nil
}

// fakePublisher records every Publish call; never fails unless told to.
type fakePublisher struct {
	mu    sync.Mutex
	calls []publishCall
}

type publishCall struct {
	stream string
	fields map[string]string
}

func (f *fakePublisher) Publish(_ context.Context, stream string, fields map[string]string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, publishCall{stream: stream, fields: fields})
	return nil
}

func (f *fakePublisher) count() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.calls)
}

func (f *fakePublisher) last() publishCall {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.calls[len(f.calls)-1]
}

func newTestClient(store EntityWriter, hashes HashCache, pub *fakePublisher) *Client {
	return &Client{
		AccountID: "acct-1", GroupID: "group-1", ServerIP: "203.0.113.5:7777",
		store: store, hashCache: hashes, stream: pub, log: testLogger(),
	}
}

func structureEntity(x, y, z float64) protocol.Entity {
	return protocol.Entity{
		Cat: protocol.CategoryStructure, ClassName: "MetalWall_C",
		Team: 42, X: x, Y: y, Z: z,
	}
}

func TestMaybeStream_NewEntityPublishesImmediately(t *testing.T) {
	store := &fakeEntityWriter{}
	hashes := newFakeHashCache()
	pub := &fakePublisher{}
	c := newTestClient(store, hashes, pub)

	e := structureEntity(100, 200, 300)
	e.Key = "structure:seed"
	c.maybeStream(context.Background(), e, time.Now(), "")

	if got := pub.count(); got != 1 {
		t.Fatalf("expected 1 publish for a brand-new entity, got %d", got)
	}
}

func TestMaybeStream_UnchangedEntityDoesNotRepublishBeforeKeyframe(t *testing.T) {
	store := &fakeEntityWriter{}
	hashes := newFakeHashCache()
	pub := &fakePublisher{}
	c := newTestClient(store, hashes, pub)

	e := structureEntity(100, 200, 300)
	e.Key = "structure:seed"
	now := time.Now()
	c.maybeStream(context.Background(), e, now, "")
	c.maybeStream(context.Background(), e, now.Add(time.Second), "")

	if got := pub.count(); got != 1 {
		t.Fatalf("expected exactly 1 publish for an unchanged entity re-sent a second later, got %d", got)
	}
}

func TestMaybeStream_KeyframeForcesRepublishEvenUnchanged(t *testing.T) {
	store := &fakeEntityWriter{}
	hashes := newFakeHashCache()
	pub := &fakePublisher{}
	c := newTestClient(store, hashes, pub)

	e := structureEntity(100, 200, 300)
	e.Key = "structure:seed"
	now := time.Now()
	c.maybeStream(context.Background(), e, now, "")
	c.maybeStream(context.Background(), e, now.Add(streamKeyframe+time.Second), "")

	if got := pub.count(); got != 2 {
		t.Fatalf("expected keyframe expiry to force a second publish, got %d publishes", got)
	}
}

func TestMaybeStream_ChangedPositionPublishesImmediately(t *testing.T) {
	store := &fakeEntityWriter{}
	hashes := newFakeHashCache()
	pub := &fakePublisher{}
	c := newTestClient(store, hashes, pub)

	now := time.Now()
	e1 := structureEntity(0, 0, 0)
	e1.Key = "structure:seed"
	c.maybeStream(context.Background(), e1, now, "")

	// Move far enough to land in a different 300-unit grid cell.
	e2 := structureEntity(1000, 0, 0)
	e2.Key = "structure:seed"
	c.maybeStream(context.Background(), e2, now.Add(time.Second), "")

	if got := pub.count(); got != 2 {
		t.Fatalf("expected a significant position change to publish immediately, got %d publishes", got)
	}
}

func TestMaybeStream_PlayerWithoutStableIDNeverPublishes(t *testing.T) {
	store := &fakeEntityWriter{}
	hashes := newFakeHashCache()
	pub := &fakePublisher{}
	c := newTestClient(store, hashes, pub)

	// No StableID -- nothing to key player.platform_id on, must be
	// dropped rather than publish a bogus platform_id="0" row.
	e := protocol.Entity{Cat: protocol.CategoryPlayer, Key: "player:steve:42", Label: "Steve", Team: 42}
	c.maybeStream(context.Background(), e, time.Now(), "")

	if got := pub.count(); got != 0 {
		t.Fatalf("expected a player entity with no StableID to never reach the stream, got %d publishes", got)
	}
}

func TestMaybeStream_PlayerWithStableIDPublishesToPlayerStream(t *testing.T) {
	store := &fakeEntityWriter{}
	hashes := newFakeHashCache()
	pub := &fakePublisher{}
	c := newTestClient(store, hashes, pub)

	e := protocol.Entity{
		Cat: protocol.CategoryPlayer, Key: "player:steve:42", Label: "Steve", Team: 42,
		X: 100, Y: 200, Z: 300, StableID: 555,
	}
	c.maybeStream(context.Background(), e, time.Now(), "")

	if got := pub.count(); got != 1 {
		t.Fatalf("expected exactly 1 publish for a player with a StableID, got %d", got)
	}
	call := pub.last()
	if call.stream != "ark:stream:player_sighting" {
		t.Fatalf("expected player entity to route to the player stream, got %q", call.stream)
	}
	if call.fields["platform_id"] != "555" {
		t.Fatalf("expected platform_id=555, got fields=%v", call.fields)
	}
	if call.fields["character_name"] != "Steve" {
		t.Fatalf("expected character_name=Steve, got fields=%v", call.fields)
	}
	if _, hasObjectHash := call.fields["object_hash"]; hasObjectHash {
		t.Fatalf("player fields must not carry structure/dino-shaped object_hash, got fields=%v", call.fields)
	}
}

func TestMaybeStream_TurretDynamicStateDoesNotChangeHash(t *testing.T) {
	store := &fakeEntityWriter{}
	hashes := newFakeHashCache()
	pub := &fakePublisher{}
	c := newTestClient(store, hashes, pub)

	now := time.Now()
	ammo1 := int32(50)
	e1 := protocol.Entity{
		Cat: protocol.CategoryTurret, ClassName: "AutoTurret_C", Key: "turret:seed",
		Team: 42, X: 0, Y: 0, Z: 0,
		Turret: &protocol.TurretInfo{Ammo: &ammo1, Powered: true},
	}
	c.maybeStream(context.Background(), e1, now, "")

	ammo2 := int32(10) // ammo ticked down, position/class unchanged
	e2 := e1
	e2.Turret = &protocol.TurretInfo{Ammo: &ammo2, Powered: true}
	c.maybeStream(context.Background(), e2, now.Add(time.Second), "")

	if got := pub.count(); got != 1 {
		t.Fatalf("expected ammo depletion alone not to trigger a republish, got %d publishes", got)
	}
}

func TestHandleSighting_UpsertsAlwaysHappenRegardlessOfDedup(t *testing.T) {
	store := &fakeEntityWriter{}
	hashes := newFakeHashCache()
	pub := &fakePublisher{}
	h := New(testLogger())
	c := newTestClient(store, hashes, pub)
	c.hub = h

	e := structureEntity(100, 200, 300)
	msg := protocol.Inbound{Type: protocol.MsgSighting, Entities: []protocol.Entity{e}}
	fillComputedKeys(msg.Entities)
	c.handleSighting(context.Background(), msg)
	c.handleSighting(context.Background(), msg)

	if got := store.count(); got != 2 {
		t.Fatalf("expected Upsert on every tick regardless of stream dedup, got %d calls", got)
	}
	if got := pub.count(); got != 1 {
		t.Fatalf("expected the stream to be deduped on the second identical tick, got %d publishes", got)
	}
}

func TestHandleSighting_ForwardsReporterIdentityAndPosition(t *testing.T) {
	store := &fakeEntityWriter{}
	hashes := newFakeHashCache()
	pub := &fakePublisher{}
	h := New(testLogger())

	sender := newTestClient(store, hashes, pub)
	sender.hub = h
	h.Register(sender)
	defer h.Unregister(sender)

	// newTestClient doesn't init send (only NewClient does) -- a nil
	// channel is never selectable, so Broadcast would just drop this
	// client via closeSlow() instead of actually delivering anything to
	// read back.
	receiver := newTestClient(store, hashes, pub)
	receiver.AccountID = "acct-2"
	receiver.send = make(chan []byte, 4)
	h.Register(receiver)
	defer h.Unregister(receiver)

	e := structureEntity(100, 200, 300)
	msg := protocol.Inbound{
		Type: protocol.MsgSighting, Entities: []protocol.Entity{e},
		ReporterCharacterID: "42", ReporterX: 10, ReporterY: 20, ReporterZ: 30,
	}
	fillComputedKeys(msg.Entities)
	sender.handleSighting(context.Background(), msg)

	select {
	case raw := <-receiver.send:
		var out protocol.Outbound
		if err := json.Unmarshal(raw, &out); err != nil {
			t.Fatalf("failed to decode broadcast payload: %v", err)
		}
		if out.ReporterCharacterID != "42" {
			t.Fatalf("expected ReporterCharacterID to be forwarded, got %q", out.ReporterCharacterID)
		}
		if out.ReporterX != 10 || out.ReporterY != 20 || out.ReporterZ != 30 {
			t.Fatalf("expected reporter position to be forwarded, got (%v,%v,%v)",
				out.ReporterX, out.ReporterY, out.ReporterZ)
		}
	default:
		t.Fatal("expected a broadcast to reach the second client registered in the same room")
	}
}

func TestStreamVanished_PublishesRemovedRecord(t *testing.T) {
	store := &fakeEntityWriter{}
	hashes := newFakeHashCache()
	pub := &fakePublisher{}
	c := newTestClient(store, hashes, pub)

	entities := []protocol.Entity{structureEntity(100, 200, 300)}
	fillComputedKeys(entities)
	key := entities[0].Key

	c.streamVanished(context.Background(), key, time.Now())

	if got := pub.count(); got != 1 {
		t.Fatalf("expected exactly 1 publish for a vanished key, got %d", got)
	}
	call := pub.last()
	if call.stream != "ark:stream:structure_sighting" {
		t.Fatalf("expected vanished structure key to route to structure stream, got %q", call.stream)
	}
	if call.fields["removed"] != "true" {
		t.Fatalf("expected removed=true field, got fields=%v", call.fields)
	}
	if call.fields["object_hash"] == "" {
		t.Fatal("expected object_hash to be recovered from the vanished key")
	}
}

func TestStreamVanished_DinoKeyNotForwarded(t *testing.T) {
	store := &fakeEntityWriter{}
	hashes := newFakeHashCache()
	pub := &fakePublisher{}
	c := newTestClient(store, hashes, pub)

	// Vanished signals are only forwarded for structure/turret, whose
	// removal is a durable fact worth a Postgres row. A dino key -- whatever
	// shape it arrives in, client-supplied or rewritten by
	// dedup.KeyForDino -- is never forwarded: wild dinos aren't persisted at
	// all, and a tame that walked out of view hasn't been destroyed.
	c.streamVanished(context.Background(), "dino:Rexy:42", time.Now())

	if got := pub.count(); got != 0 {
		t.Fatalf("expected a dino vanished-key to never be forwarded (no recoverable object_hash), got %d publishes", got)
	}
}

// fillComputedKeys overwrites the dino key even when the client sent one --
// that retroactively fixes clients already in the field, which is the whole
// point of doing it server-side.
func TestFillComputedKeys_OverwritesCollidingDinoKeys(t *testing.T) {
	entities := []protocol.Entity{
		{Cat: protocol.CategoryDino, Key: "dino:Rex:0", ClassName: "Rex_Character_BP_C", X: 0, Y: 0, Z: 0},
		{Cat: protocol.CategoryDino, Key: "dino:Rex:0", ClassName: "Rex_Character_BP_C", X: 50_000, Y: 0, Z: 0},
	}
	fillComputedKeys(entities)

	if entities[0].Key == entities[1].Key {
		t.Fatalf("expected two rexes far apart to stop sharing a key, both were %q", entities[0].Key)
	}
	for i, e := range entities {
		if e.Key == "dino:Rex:0" {
			t.Errorf("entity %d kept the colliding client-supplied key", i)
		}
	}
}

func TestFillComputedKeys_LeavesPlayerKeyAlone(t *testing.T) {
	// A player's key carries a real per-account stable_id, not a guess.
	entities := []protocol.Entity{
		{Cat: protocol.CategoryPlayer, Key: "player:8842091337:42", Label: "Steve", StableID: 8842091337},
	}
	fillComputedKeys(entities)

	if entities[0].Key != "player:8842091337:42" {
		t.Fatalf("expected the player key untouched, got %q", entities[0].Key)
	}
}
