package ws

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/igm/sockjs-go/v3/sockjs"
)

// outboundBuffer bounds how many frames may queue for one client. Price ticks
// arrive continuously, so a client that cannot keep up drops frames rather
// than stalling the publisher for everyone else.
const outboundBuffer = 256

// Broker is the in-memory topic broker, equivalent to Spring's simple broker
// with a "/topic" prefix. Destinations are matched exactly, which is all the
// frontend needs.
type Broker struct {
	mu      sync.RWMutex
	clients map[*client]struct{}

	messageID atomic.Int64
}

// NewBroker creates an empty broker.
func NewBroker() *Broker {
	return &Broker{clients: make(map[*client]struct{})}
}

// sessionWriter is the slice of sockjs.Session the broker needs. Depending on
// an interface rather than the concrete type keeps the STOMP logic testable
// without a live SockJS transport.
type sessionWriter interface {
	Send(string) error
	ID() string
}

// client is one STOMP session over one SockJS connection.
type client struct {
	session sessionWriter

	mu   sync.RWMutex
	subs map[string]string // subscription id -> destination

	out       chan string
	closeOnce sync.Once
	done      chan struct{}
}

func newClient(session sessionWriter) *client {
	return &client{
		session: session,
		subs:    make(map[string]string),
		out:     make(chan string, outboundBuffer),
		done:    make(chan struct{}),
	}
}

// enqueue hands a frame to the writer goroutine, dropping it if the client is
// too far behind.
func (c *client) enqueue(frame string) {
	select {
	case <-c.done:
	case c.out <- frame:
	default:
		slog.Warn("ws client outbound buffer full, dropping frame", "session", c.session.ID())
	}
}

// writeLoop serialises all writes for one session.
func (c *client) writeLoop() {
	for {
		select {
		case <-c.done:
			return
		case frame := <-c.out:
			if err := c.session.Send(frame); err != nil {
				c.close()
				return
			}
		}
	}
}

func (c *client) close() {
	c.closeOnce.Do(func() { close(c.done) })
}

func (c *client) subscribe(id, destination string) {
	c.mu.Lock()
	c.subs[id] = destination
	c.mu.Unlock()
}

func (c *client) unsubscribe(id string) {
	c.mu.Lock()
	delete(c.subs, id)
	c.mu.Unlock()
}

// subscriptionsFor returns the ids subscribed to a destination.
func (c *client) subscriptionsFor(destination string) []string {
	c.mu.RLock()
	defer c.mu.RUnlock()

	var ids []string
	for id, dest := range c.subs {
		if dest == destination {
			ids = append(ids, id)
		}
	}
	return ids
}

// Handler returns the HTTP handler serving the SockJS endpoint. Mount it at
// the same "/ws" path the Java application used.
func (b *Broker) Handler(prefix string) *sockjs.Handler {
	options := sockjs.DefaultOptions
	// The Java endpoint used setAllowedOriginPatterns("*").
	options.CheckOrigin = func(*http.Request) bool { return true }
	options.Websocket = true
	// Without a write timeout a stalled connection can block its writer
	// goroutine indefinitely.
	options.WebsocketWriteTimeout = 10 * time.Second

	return sockjs.NewHandler(prefix, options, b.serve)
}

// serve runs one SockJS session for its whole lifetime.
func (b *Broker) serve(session sockjs.Session) {
	b.serveSession(session)
}

// serveSession contains the transport-independent session loop.
func (b *Broker) serveSession(session interface {
	sessionWriter
	Recv() (string, error)
}) {
	c := newClient(session)

	b.add(c)
	go c.writeLoop()

	defer func() {
		b.remove(c)
		c.close()
	}()

	for {
		payload, err := session.Recv()
		if err != nil {
			return
		}

		for _, raw := range splitFrames(payload) {
			f, err := parseFrame(raw)
			if err != nil {
				continue
			}
			if closed := b.dispatch(c, f); closed {
				return
			}
		}
	}
}

// dispatch handles one client frame. It reports true when the session should
// be torn down.
func (b *Broker) dispatch(c *client, f *frame) bool {
	switch f.command {
	case cmdConnect, cmdStomp:
		// Negotiating 0,0 disables heartbeats in both directions, which keeps
		// the implementation simple; SockJS already keeps the transport alive.
		c.enqueue((&frame{
			command: cmdConnected,
			headers: map[string]string{
				"version":    "1.2",
				"heart-beat": "0,0",
			},
		}).encode())

	case cmdSubscribe:
		id, destination := f.header("id"), f.header("destination")
		if id == "" || destination == "" {
			c.enqueue(errorFrame("SUBSCRIBE requires id and destination headers"))
			return false
		}
		c.subscribe(id, destination)

	case cmdUnsubscribe:
		if id := f.header("id"); id != "" {
			c.unsubscribe(id)
		}

	case cmdSend:
		// Spring routed these to @MessageMapping handlers under "/app".
		// Nothing consumed them, so they are acknowledged and ignored.
		slog.Debug("ignoring inbound STOMP SEND", "destination", f.header("destination"))

	case cmdDisconnect:
		if receipt := f.header("receipt"); receipt != "" {
			c.enqueue((&frame{
				command: cmdReceipt,
				headers: map[string]string{"receipt-id": receipt},
			}).encode())
		}
		return true
	}

	if receipt := f.header("receipt"); receipt != "" && f.command != cmdDisconnect {
		c.enqueue((&frame{
			command: cmdReceipt,
			headers: map[string]string{"receipt-id": receipt},
		}).encode())
	}

	return false
}

// Publish serialises payload as JSON and sends it to every subscriber of
// destination. It replaces SimpMessagingTemplate.convertAndSend.
func (b *Broker) Publish(destination string, payload any) {
	body, err := json.Marshal(payload)
	if err != nil {
		slog.Error("failed to marshal websocket payload", "destination", destination, "error", err)
		return
	}

	b.mu.RLock()
	defer b.mu.RUnlock()

	for c := range b.clients {
		for _, subscriptionID := range c.subscriptionsFor(destination) {
			c.enqueue((&frame{
				command: cmdMessage,
				headers: map[string]string{
					"destination":    destination,
					"subscription":   subscriptionID,
					"message-id":     strconv.FormatInt(b.messageID.Add(1), 10),
					"content-type":   "application/json;charset=UTF-8",
					"content-length": strconv.Itoa(len(body)),
				},
				body: string(body),
			}).encode())
		}
	}
}

func (b *Broker) add(c *client) {
	b.mu.Lock()
	b.clients[c] = struct{}{}
	b.mu.Unlock()
}

func (b *Broker) remove(c *client) {
	b.mu.Lock()
	delete(b.clients, c)
	b.mu.Unlock()
}

func errorFrame(message string) string {
	return (&frame{
		command: cmdError,
		headers: map[string]string{"message": message},
		body:    fmt.Sprintf("%s\n", message),
	}).encode()
}
