package ws

import (
	"strings"
	"testing"
)

func TestParseFrameReadsCommandHeadersAndBody(t *testing.T) {
	raw := "SEND\ndestination:/app/echo\ncontent-type:application/json\n\n{\"a\":1}"

	f, err := parseFrame(raw)
	if err != nil {
		t.Fatalf("parseFrame returned error: %v", err)
	}

	if f.command != "SEND" {
		t.Errorf("command = %q, want SEND", f.command)
	}
	if got := f.header("destination"); got != "/app/echo" {
		t.Errorf("destination = %q, want /app/echo", got)
	}
	if f.body != `{"a":1}` {
		t.Errorf("body = %q, want {\"a\":1}", f.body)
	}
}

func TestParseFrameHandlesLeadingHeartbeatNewline(t *testing.T) {
	// stompjs may prepend a heartbeat newline to the next frame.
	f, err := parseFrame("\nSUBSCRIBE\nid:sub-0\ndestination:/topic/price/BTCUSDT\n\n")
	if err != nil {
		t.Fatalf("parseFrame returned error: %v", err)
	}

	if f.command != cmdSubscribe {
		t.Errorf("command = %q, want %q", f.command, cmdSubscribe)
	}
	if got := f.header("id"); got != "sub-0" {
		t.Errorf("id = %q, want sub-0", got)
	}
}

func TestSplitFramesSeparatesOnNullAndDropsHeartbeats(t *testing.T) {
	payload := "CONNECT\n\n" + nullTerminator + "\n" + nullTerminator +
		"SUBSCRIBE\nid:sub-0\ndestination:/topic/x\n\n" + nullTerminator

	frames := splitFrames(payload)
	if len(frames) != 2 {
		t.Fatalf("got %d frames, want 2: %#v", len(frames), frames)
	}

	if !strings.HasPrefix(frames[0], "CONNECT") {
		t.Errorf("first frame = %q, want a CONNECT frame", frames[0])
	}
	if !strings.Contains(frames[1], "SUBSCRIBE") {
		t.Errorf("second frame = %q, want a SUBSCRIBE frame", frames[1])
	}
}

func TestEncodeProducesNullTerminatedFrame(t *testing.T) {
	encoded := (&frame{
		command: cmdMessage,
		headers: map[string]string{"destination": "/topic/price/BTCUSDT"},
		body:    `{"price":1}`,
	}).encode()

	if !strings.HasPrefix(encoded, "MESSAGE\n") {
		t.Errorf("encoded frame does not start with the command: %q", encoded)
	}
	if !strings.HasSuffix(encoded, nullTerminator) {
		t.Errorf("encoded frame is not NULL terminated: %q", encoded)
	}
	if !strings.Contains(encoded, "destination:/topic/price/BTCUSDT\n") {
		t.Errorf("encoded frame is missing the destination header: %q", encoded)
	}
	if !strings.Contains(encoded, "\n\n"+`{"price":1}`) {
		t.Errorf("body is not separated from headers by a blank line: %q", encoded)
	}
}

func TestHeaderEscapingRoundTrips(t *testing.T) {
	original := "value:with\nspecial\\chars"

	if got := unescapeHeader(escapeHeader(original)); got != original {
		t.Errorf("round trip = %q, want %q", got, original)
	}
}
