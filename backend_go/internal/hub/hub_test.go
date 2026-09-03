package hub

import (
	"testing"
)

// newRegisteredClient builds a minimal Client good enough to register with
// a Hub and receive broadcasts — no real transport connection, send is a
// buffered channel a test can drain directly.
func newRegisteredClient(h *Hub, accountID, groupID, serverIP string) *Client {
	c := &Client{
		AccountID: accountID, GroupID: groupID, ServerIP: serverIP,
		hub: h, log: testLogger(), send: make(chan []byte, 8),
	}
	h.Register(c)
	return c
}

func TestBroadcast_SameRoomReceivesSameRoomDoesNotCrossServers(t *testing.T) {
	h := New(testLogger())
	a := newRegisteredClient(h, "acct-a", "group-1", "203.0.113.5:7777")      // server X
	b := newRegisteredClient(h, "acct-b", "group-1", "203.0.113.5:7777")      // server X, same room as a
	other := newRegisteredClient(h, "acct-c", "group-1", "198.51.100.9:7777") // server Y, same group, different server

	h.Broadcast("group-1", "203.0.113.5:7777", []byte("payload"), a)

	select {
	case got := <-b.send:
		if string(got) != "payload" {
			t.Fatalf("unexpected payload: %s", got)
		}
	default:
		t.Fatal("expected same-room client to receive the broadcast")
	}

	select {
	case got := <-other.send:
		t.Fatalf("expected different-server client to receive nothing, got %s", got)
	default:
	}
}

func TestBroadcast_NeverEchoesToSender(t *testing.T) {
	h := New(testLogger())
	a := newRegisteredClient(h, "acct-a", "group-1", "203.0.113.5:7777")

	h.Broadcast("group-1", "203.0.113.5:7777", []byte("payload"), a)

	select {
	case got := <-a.send:
		t.Fatalf("expected the reporting client to never receive its own broadcast, got %s", got)
	default:
	}
}

func TestRevokeAccount_ClosesAllRoomsForThatGroupRegardlessOfServer(t *testing.T) {
	h := New(testLogger())
	target := newRegisteredClient(h, "kicked", "group-1", "203.0.113.5:7777")
	sameAccountOtherServer := newRegisteredClient(h, "kicked", "group-1", "198.51.100.9:7777")
	otherAccount := newRegisteredClient(h, "innocent", "group-1", "203.0.113.5:7777")
	otherGroup := newRegisteredClient(h, "kicked", "group-2", "203.0.113.5:7777")

	closed := h.RevokeAccount("group-1", "kicked")
	if closed != 2 {
		t.Fatalf("expected 2 connections closed (same account, both servers, same group), got %d", closed)
	}

	// closeSlow closes the underlying conn, which is nil here in tests --
	// verify indirectly via closeOnce having fired instead of calling
	// c.conn.Close() through the real path.
	for _, c := range []*Client{target, sameAccountOtherServer} {
		fired := false
		c.closeOnce.Do(func() { fired = true })
		if fired {
			t.Fatalf("expected closeOnce to have already fired for a revoked client")
		}
	}
	for _, c := range []*Client{otherAccount, otherGroup} {
		fired := false
		c.closeOnce.Do(func() { fired = true })
		if !fired {
			t.Fatalf("expected closeOnce to NOT have fired yet for an unrelated client")
		}
	}
}

func TestUnregister_PrunesEmptyRoom(t *testing.T) {
	h := New(testLogger())
	c := newRegisteredClient(h, "acct-a", "group-1", "203.0.113.5:7777")

	h.Unregister(c)

	h.mu.RLock()
	_, exists := h.rooms[roomKey{GroupID: "group-1", ServerIP: "203.0.113.5:7777"}]
	h.mu.RUnlock()
	if exists {
		t.Fatal("expected the room to be pruned once its last client disconnects")
	}
}

func TestRegister_TracksTotalClientCount(t *testing.T) {
	h := New(testLogger())
	newRegisteredClient(h, "a", "g1", "ip1")
	newRegisteredClient(h, "b", "g1", "ip1")
	newRegisteredClient(h, "c", "g2", "ip2")

	if h.clients != 3 {
		t.Fatalf("expected 3 total clients, got %d", h.clients)
	}
}
