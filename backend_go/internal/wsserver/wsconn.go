package wsserver

import (
	"time"

	"github.com/gorilla/websocket"
)

// wsConn adapts a *websocket.Conn to hub.Conn — one frame per
// ReadMessage/WriteMessage call, WS message-type framing collapsed away
// (the relay's protocol is JSON text either way; a client sending a binary
// frame with valid JSON in it is accepted the same as a text frame, since
// protocol.Decode doesn't care).
type wsConn struct {
	conn *websocket.Conn
}

// newWSConn wraps conn, applying maxBytes as the WS read-frame limit —
// enforced by gorilla itself (a client exceeding it gets its connection
// closed on the next read), not by hub.Client, which has no
// transport-specific notion of "frame too big".
func newWSConn(conn *websocket.Conn, maxBytes int64) *wsConn {
	conn.SetReadLimit(maxBytes)
	return &wsConn{conn: conn}
}

func (w *wsConn) ReadMessage() ([]byte, error) {
	_, raw, err := w.conn.ReadMessage()
	return raw, err
}

func (w *wsConn) WriteMessage(raw []byte) error {
	return w.conn.WriteMessage(websocket.TextMessage, raw)
}

func (w *wsConn) SetReadDeadline(t time.Time) error  { return w.conn.SetReadDeadline(t) }
func (w *wsConn) SetWriteDeadline(t time.Time) error { return w.conn.SetWriteDeadline(t) }

func (w *wsConn) Close() error { return w.conn.Close() }
