package hub

import "time"

// Conn abstracts one framed, full-duplex transport under a Client — one
// logical message per ReadMessage/WriteMessage call, whatever the wire
// format underneath actually is (a WebSocket text frame, or a
// length-prefixed QUIC stream — see internal/wsserver and
// internal/quicserver for the two adapters). Client's dedup/broadcast/
// revocation logic is written once against this interface and does not
// know or care which transport carried a given connection; that keeps the
// DTO-sharing plan's "Publisher interface, transport-agnostic" principle
// on the server side too, not just the arkmultitool client.
type Conn interface {
	// ReadMessage blocks for the next complete message. It returns an
	// error on any read failure, protocol violation, or closed connection
	// — the caller (readPump) always treats an error as "connection is
	// over", never distinguishing causes.
	ReadMessage() ([]byte, error)

	// WriteMessage sends one complete message. Concurrent calls are not
	// required to be safe — Client only ever writes from its single
	// writePump goroutine.
	WriteMessage(raw []byte) error

	// SetReadDeadline arms (or disarms, with a zero time.Time) the
	// deadline for the next ReadMessage call — called before every read so
	// liveness is judged the same way regardless of transport: "have we
	// heard anything at all in pongWait", not a transport-specific
	// keepalive primitive.
	SetReadDeadline(t time.Time) error

	// SetWriteDeadline arms the deadline for the next WriteMessage call —
	// called before every write so a stalled peer (buffer full, dead
	// network) can't hang writePump forever instead of tripping the send
	// channel's own backpressure handling in Hub.Broadcast.
	SetWriteDeadline(t time.Time) error

	// Close tears down the underlying transport. Safe to call more than
	// once; a second call after the first returns an error rather than
	// panicking (both adapters delegate to their library's own
	// already-idempotent Close).
	Close() error
}
