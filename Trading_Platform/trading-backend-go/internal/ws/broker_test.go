package ws

import (
	"strings"
	"sync"
	"testing"
	"time"
)

// fakeSession stands in for a SockJS session.
type fakeSession struct {
	mu   sync.Mutex
	sent []string

	incoming chan string
	closed   chan struct{}
}

func newFakeSession() *fakeSession {
	return &fakeSession{
		incoming: make(chan string, 8),
		closed:   make(chan struct{}),
	}
}

func (f *fakeSession) ID() string { return "fake-session" }

func (f *fakeSession) Send(msg string) error {
	f.mu.Lock()
	f.sent = append(f.sent, msg)
	f.mu.Unlock()
	return nil
}

func (f *fakeSession) Recv() (string, error) {
	select {
	case msg := <-f.incoming:
		return msg, nil
	case <-f.closed:
		return "", errEmptyFrame
	}
}

func (f *fakeSession) frames() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.sent...)
}

// waitForFrame polls until a frame containing needle shows up.
func (f *fakeSession) waitForFrame(t *testing.T, needle string) string {
	t.Helper()

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		for _, frame := range f.frames() {
			if strings.Contains(frame, needle) {
				return frame
			}
		}
		time.Sleep(5 * time.Millisecond)
	}

	t.Fatalf("no frame containing %q; got %#v", needle, f.frames())
	return ""
}

func TestBrokerConnectAndPublishDeliversToSubscriber(t *testing.T) {
	broker := NewBroker()
	session := newFakeSession()

	done := make(chan struct{})
	go func() {
		broker.serveSession(session)
		close(done)
	}()
	defer func() {
		close(session.closed)
		<-done
	}()

	session.incoming <- "CONNECT\naccept-version:1.2\nheart-beat:10000,10000\n\n" + nullTerminator

	connected := session.waitForFrame(t, cmdConnected)
	if !strings.Contains(connected, "heart-beat:0,0") {
		t.Errorf("CONNECTED frame should disable heartbeats, got %q", connected)
	}

	session.incoming <- "SUBSCRIBE\nid:sub-0\ndestination:/topic/price/BTCUSDT\n\n" + nullTerminator

	// Wait until the subscription is registered before publishing.
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		broker.Publish("/topic/price/BTCUSDT", map[string]string{"ticker": "BTCUSDT"})
		if frameContaining(session.frames(), cmdMessage) != "" {
			break
		}
		time.Sleep(5 * time.Millisecond)
	}

	message := session.waitForFrame(t, cmdMessage)

	for _, want := range []string{
		"destination:/topic/price/BTCUSDT",
		"subscription:sub-0",
		`{"ticker":"BTCUSDT"}`,
	} {
		if !strings.Contains(message, want) {
			t.Errorf("MESSAGE frame missing %q; got %q", want, message)
		}
	}
}

func TestBrokerDoesNotDeliverToOtherDestinations(t *testing.T) {
	broker := NewBroker()
	session := newFakeSession()

	done := make(chan struct{})
	go func() {
		broker.serveSession(session)
		close(done)
	}()
	defer func() {
		close(session.closed)
		<-done
	}()

	session.incoming <- "CONNECT\n\n" + nullTerminator
	session.waitForFrame(t, cmdConnected)

	session.incoming <- "SUBSCRIBE\nid:sub-0\ndestination:/topic/price/BTCUSDT\n\n" + nullTerminator
	time.Sleep(50 * time.Millisecond)

	broker.Publish("/topic/price/ETHUSDT", map[string]string{"ticker": "ETHUSDT"})
	time.Sleep(50 * time.Millisecond)

	if frame := frameContaining(session.frames(), "ETHUSDT"); frame != "" {
		t.Errorf("received a frame for an unsubscribed destination: %q", frame)
	}
}

func TestBrokerUnsubscribeStopsDelivery(t *testing.T) {
	broker := NewBroker()
	session := newFakeSession()

	done := make(chan struct{})
	go func() {
		broker.serveSession(session)
		close(done)
	}()
	defer func() {
		close(session.closed)
		<-done
	}()

	session.incoming <- "CONNECT\n\n" + nullTerminator
	session.waitForFrame(t, cmdConnected)

	session.incoming <- "SUBSCRIBE\nid:sub-0\ndestination:/topic/alerts/1\n\n" + nullTerminator
	session.incoming <- "UNSUBSCRIBE\nid:sub-0\n\n" + nullTerminator
	time.Sleep(50 * time.Millisecond)

	broker.Publish("/topic/alerts/1", map[string]string{"ticker": "AAPL"})
	time.Sleep(50 * time.Millisecond)

	if frame := frameContaining(session.frames(), cmdMessage); frame != "" {
		t.Errorf("received a frame after unsubscribing: %q", frame)
	}
}

func frameContaining(frames []string, needle string) string {
	for _, frame := range frames {
		if strings.Contains(frame, needle) {
			return frame
		}
	}
	return ""
}
