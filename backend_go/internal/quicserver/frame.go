package quicserver

import (
	"encoding/binary"
	"errors"
	"fmt"
	"io"
)

// lengthPrefixSize is the width of the frame-length header: a raw QUIC
// stream is just a byte pipe with no message framing of its own, so every
// message written to it needs an explicit boundary — otherwise the reader
// has no way to know where one JSON payload ends and the next begins.
const lengthPrefixSize = 4

// readFrame reads one length-prefixed frame from r. maxBytes bounds the
// length header itself before any payload is read — a corrupted or
// malicious length field costs a rejected connection, never an attempt to
// allocate or read an attacker-chosen amount of memory (Engineering
// Directive: "a corrupted length field costs a rejected operation, never a
// hang").
func readFrame(r io.Reader, maxBytes int64) ([]byte, error) {
	var header [lengthPrefixSize]byte
	if _, err := io.ReadFull(r, header[:]); err != nil {
		return nil, err
	}
	length := int64(binary.BigEndian.Uint32(header[:]))
	if length < 0 || length > maxBytes {
		return nil, fmt.Errorf("quicserver: frame length %d exceeds max %d", length, maxBytes)
	}
	if length == 0 {
		return nil, errors.New("quicserver: zero-length frame")
	}
	payload := make([]byte, length)
	if _, err := io.ReadFull(r, payload); err != nil {
		return nil, err
	}
	return payload, nil
}

// writeFrame writes payload to w with its length-prefix header. Returns an
// error rather than silently truncating if payload is too large to encode
// in a uint32 — that would corrupt the stream for the reader, not just this
// one message.
func writeFrame(w io.Writer, payload []byte) error {
	if len(payload) > 0xFFFFFFFF {
		return fmt.Errorf("quicserver: payload of %d bytes exceeds uint32 length prefix", len(payload))
	}
	var header [lengthPrefixSize]byte
	binary.BigEndian.PutUint32(header[:], uint32(len(payload)))
	if _, err := w.Write(header[:]); err != nil {
		return err
	}
	_, err := w.Write(payload)
	return err
}
