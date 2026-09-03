package quicserver

// handshakeRequest is the first length-prefixed frame the client must send
// on the one stream it opens per connection. A bare QUIC connection has no
// equivalent of HTTP's Authorization header or query string, so the two
// values the relay needs before it will accept any traffic (bearer token,
// server_ip) travel as the body of an explicit first message instead. No
// group_id: the client never states one — the relay resolves it itself
// from token's account_id (see store.GroupMembership.ActiveGroup).
type handshakeRequest struct {
	Token    string `json:"token"`
	ServerIP string `json:"server_ip"`
}

// handshakeResponse acks or rejects the handshake before any sighting
// traffic is accepted. Error is human-readable, not a machine code: this
// is a devtool/first-party-client protocol, not a public API surface with
// a stability contract on error shapes.
type handshakeResponse struct {
	OK    bool   `json:"ok"`
	Error string `json:"error,omitempty"`
}

// maxHandshakeBytes bounds the handshake frame itself, independent of the
// data-plane maxBytes (which is usually much larger, to fit a batch of
// sightings) — a handshake is three short strings, never legitimately
// large.
const maxHandshakeBytes = 4096
