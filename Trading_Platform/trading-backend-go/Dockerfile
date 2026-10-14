# Build a static binary, then ship it on a minimal base image.
FROM golang:1.27-alpine AS build

WORKDIR /src

# Cache dependencies before copying the rest of the source.
COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/server ./cmd/server

FROM gcr.io/distroless/static-debian12:nonroot

WORKDIR /app
COPY --from=build /out/server /app/server

USER nonroot:nonroot
EXPOSE 8080

ENTRYPOINT ["/app/server"]
