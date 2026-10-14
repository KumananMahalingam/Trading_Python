package ws

import (
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

// TestSockJSStompEndToEnd drives the broker through a real SockJS WebSocket
// transport, the same path sockjs-client and @stomp/stompjs take in the
// browser. It is the guard that the frontend keeps working unchanged.
func TestSockJSStompEndToEnd(t *testing.T) {
	broker := NewBroker()

	server := httptest.NewServer(broker.Handler("/ws"))
	defer server.Close()

	// SockJS websocket transport path: /{prefix}/{server}/{session}/websocket
	url := strings.Replace(server.URL, "http://", "ws://", 1) + "/ws/000/e2esession/websocket"

	conn, _, err := websocket.DefaultDialer.Dial(url, nil)
	if err != nil {
		t.Fatalf("failed to dial SockJS endpoint: %v", err)
	}
	defer conn.Close()

	if err := conn.SetReadDeadline(time.Now().Add(5 * time.Second)); err != nil {
		t.Fatalf("failed to set read deadline: %v", err)
	}

	// SockJS opens with the "o" frame.
	if open := readRawFrame(t, conn); open != "o" {
		t.Fatalf("first SockJS frame = %q, want \"o\"", open)
	}

	sendStompFrame(t, conn, "CONNECT\naccept-version:1.0,1.1,1.2\nheart-beat:10000,10000\n\n"+nullTerminator)

	connected := readStompMessage(t, conn)
	if !strings.HasPrefix(connected, cmdConnected) {
		t.Fatalf("expected a CONNECTED frame, got %q", connected)
	}

	sendStompFrame(t, conn, "SUBSCRIBE\nid:sub-0\ndestination:/topic/price/BTCUSDT\n\n"+nullTerminator)

	// Publish until the subscription has been processed by the session loop.
	published := make(chan struct{})
	stopPublishing := make(chan struct{})
	go func() {
		defer close(published)
		ticker := time.NewTicker(20 * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-stopPublishing:
				return
			case <-ticker.C:
				broker.Publish("/topic/price/BTCUSDT", map[string]any{
					"ticker": "BTCUSDT",
					"price":  42,
				})
			}
		}
	}()

	message := readStompMessage(t, conn)
	close(stopPublishing)
	<-published

	if !strings.HasPrefix(message, cmdMessage) {
		t.Fatalf("expected a MESSAGE frame, got %q", message)
	}
	for _, want := range []string{
		"destination:/topic/price/BTCUSDT",
		"subscription:sub-0",
		`"ticker":"BTCUSDT"`,
	} {
		if !strings.Contains(message, want) {
			t.Errorf("MESSAGE frame missing %q; got %q", want, message)
		}
	}
}

// sendStompFrame writes a STOMP frame using SockJS framing, which expects a
// JSON array of strings.
func sendStompFrame(t *testing.T, conn *websocket.Conn, frame string) {
	t.Helper()

	if err := conn.WriteJSON([]string{frame}); err != nil {
		t.Fatalf("failed to write frame: %v", err)
	}
}

func readRawFrame(t *testing.T, conn *websocket.Conn) string {
	t.Helper()

	_, payload, err := conn.ReadMessage()
	if err != nil {
		t.Fatalf("failed to read frame: %v", err)
	}
	return string(payload)
}

// readStompMessage unwraps SockJS "a[...]" array frames, skipping heartbeats,
// and returns the first STOMP frame inside.
func readStompMessage(t *testing.T, conn *websocket.Conn) string {
	t.Helper()

	for {
		raw := readRawFrame(t, conn)

		switch {
		case raw == "h": // SockJS heartbeat
			continue
		case strings.HasPrefix(raw, "a"):
			var messages []string
			if err := json.Unmarshal([]byte(raw[1:]), &messages); err != nil {
				t.Fatalf("failed to decode SockJS array frame %q: %v", raw, err)
			}
			if len(messages) == 0 {
				continue
			}
			return strings.TrimSuffix(messages[0], nullTerminator)
		default:
			t.Fatalf("unexpected SockJS frame %q", raw)
		}
	}
}
