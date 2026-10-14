// Package ws implements the WebSocket messaging layer.
//
// The React frontend talks STOMP over SockJS (@stomp/stompjs + sockjs-client),
// which is what Spring's @EnableWebSocketMessageBroker exposed. Go has no
// equivalent broker, so this package implements the subset of STOMP 1.2 the
// client actually uses: CONNECT, SUBSCRIBE, UNSUBSCRIBE, SEND and DISCONNECT,
// plus server-sent MESSAGE frames. That keeps the frontend untouched.
package ws

import (
	"errors"
	"strings"
)

// STOMP commands handled by the server.
const (
	cmdConnect     = "CONNECT"
	cmdStomp       = "STOMP"
	cmdSubscribe   = "SUBSCRIBE"
	cmdUnsubscribe = "UNSUBSCRIBE"
	cmdSend        = "SEND"
	cmdDisconnect  = "DISCONNECT"
)

// Commands produced by the server.
const (
	cmdConnected = "CONNECTED"
	cmdMessage   = "MESSAGE"
	cmdReceipt   = "RECEIPT"
	cmdError     = "ERROR"
)

// nullTerminator ends every STOMP frame on the wire.
const nullTerminator = "\x00"

var errEmptyFrame = errors.New("empty frame")

// frame is a parsed STOMP frame.
type frame struct {
	command string
	headers map[string]string
	body    string
}

func (f *frame) header(name string) string { return f.headers[name] }

// encode renders the frame for the wire, including the NULL terminator.
func (f *frame) encode() string {
	var b strings.Builder

	b.WriteString(f.command)
	b.WriteString("\n")

	for name, value := range f.headers {
		b.WriteString(escapeHeader(name))
		b.WriteString(":")
		b.WriteString(escapeHeader(value))
		b.WriteString("\n")
	}

	b.WriteString("\n")
	b.WriteString(f.body)
	b.WriteString(nullTerminator)

	return b.String()
}

// splitFrames divides a SockJS payload into individual STOMP frames. A single
// payload can carry more than one frame, and bare newlines are heartbeats.
func splitFrames(payload string) []string {
	parts := strings.Split(payload, nullTerminator)

	frames := make([]string, 0, len(parts))
	for _, part := range parts {
		if strings.TrimSpace(part) == "" {
			// Heartbeat or trailing padding, nothing to act on.
			continue
		}
		frames = append(frames, part)
	}

	return frames
}

// parseFrame reads a single frame with its NULL terminator already removed.
func parseFrame(raw string) (*frame, error) {
	// A preceding heartbeat newline may still be attached to the frame.
	raw = strings.TrimLeft(raw, "\r\n")
	if raw == "" {
		return nil, errEmptyFrame
	}

	head, body := raw, ""
	if idx := strings.Index(raw, "\n\n"); idx >= 0 {
		head, body = raw[:idx], raw[idx+2:]
	} else if idx := strings.Index(raw, "\r\n\r\n"); idx >= 0 {
		head, body = raw[:idx], raw[idx+4:]
	}

	lines := strings.Split(strings.ReplaceAll(head, "\r\n", "\n"), "\n")
	if len(lines) == 0 || strings.TrimSpace(lines[0]) == "" {
		return nil, errEmptyFrame
	}

	f := &frame{
		command: strings.TrimSpace(lines[0]),
		headers: make(map[string]string, len(lines)-1),
		// Bodies are NULL terminated; any trailing NULL is already stripped.
		body: body,
	}

	for _, line := range lines[1:] {
		if line == "" {
			continue
		}
		name, value, found := strings.Cut(line, ":")
		if !found {
			continue
		}
		// Repeated headers: the first occurrence wins, per the spec.
		key := unescapeHeader(name)
		if _, exists := f.headers[key]; !exists {
			f.headers[key] = unescapeHeader(value)
		}
	}

	return f, nil
}

// escapeHeader applies STOMP 1.2 header escaping.
func escapeHeader(value string) string {
	replacer := strings.NewReplacer(
		"\\", "\\\\",
		"\r", "\\r",
		"\n", "\\n",
		":", "\\c",
	)
	return replacer.Replace(value)
}

// unescapeHeader reverses STOMP 1.2 header escaping.
func unescapeHeader(value string) string {
	replacer := strings.NewReplacer(
		"\\r", "\r",
		"\\n", "\n",
		"\\c", ":",
		"\\\\", "\\",
	)
	return replacer.Replace(value)
}
