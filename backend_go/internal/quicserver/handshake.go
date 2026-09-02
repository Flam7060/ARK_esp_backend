package quicserver

// handshakeRequest is the first length-prefixed frame the client must send
// on the one stream it opens per connection. A bare QUIC connection has no
// equivalent of HTTP's Authorization header or query string (that's the WS
// path's mechanism, see wsserver.Handler) — so the same three values
// (bearer token, group_id, server_ip) that travel as WS connect params
// travel here as the body of an explicit first message instead.
type handshakeRequest struct {
	Token    string `json:"token"`
	GroupID  string `json:"group_id"`
	ServerIP string `json:"server_ip"`
}

// handshakeResponse acks or rejects the handshake before any sighting
// traffic is accepted — the QUIC-side equivalent of the WS path's HTTP
// status code on a failed Upgrade. Error is human-readable, not a machine
// code: this is a devtool/first-party-client protocol, not a public API
// surface with a stability contract on error shapes.
type handshakeResponse struct {
	OK    bool   `json:"ok"`
	Error string `json:"error,omitempty"`
}

// maxHandshakeBytes bounds the handshake frame itself, independent of the
// data-plane maxBytes (which is usually much larger, to fit a batch of
// sightings) — a handshake is three short strings, never legitimately
// large.
const maxHandshakeBytes = 4096
