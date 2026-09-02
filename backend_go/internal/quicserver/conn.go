package quicserver

import (
	"time"

	"github.com/quic-go/quic-go"
)

// quicConn adapts one QUIC stream to hub.Conn, framing each message with a
// 4-byte length prefix (frame.go) — the QUIC-side mirror of wsserver's
// wsConn, so hub.Client's dedup/broadcast logic runs unchanged over either
// transport.
type quicConn struct {
	stream   *quic.Stream
	maxBytes int64
}

func newQUICConn(stream *quic.Stream, maxBytes int64) *quicConn {
	return &quicConn{stream: stream, maxBytes: maxBytes}
}

func (q *quicConn) ReadMessage() ([]byte, error) {
	return readFrame(q.stream, q.maxBytes)
}

func (q *quicConn) WriteMessage(raw []byte) error {
	return writeFrame(q.stream, raw)
}

func (q *quicConn) SetReadDeadline(t time.Time) error  { return q.stream.SetReadDeadline(t) }
func (q *quicConn) SetWriteDeadline(t time.Time) error { return q.stream.SetWriteDeadline(t) }

// Close cancels both directions of the stream rather than a plain
// stream.Close (which only half-closes the send side and waits for the
// peer to acknowledge) — closeSlow/writePump's teardown wants the
// connection gone now, not a graceful FIN handshake with a peer that may
// already be unresponsive. Cancel is unconditional and returns nothing to
// fail on the caller's side (idempotent by design — see hub.Conn.Close's
// doc on being safe to call more than once).
func (q *quicConn) Close() error {
	q.stream.CancelRead(0)
	q.stream.CancelWrite(0)
	return nil
}
