# syntax=docker/dockerfile:1
FROM golang:1.26-bookworm AS builder
WORKDIR /src

# Зависимости отдельным слоем — правка кода не тянет повторную скачку модулей.
COPY go.mod go.sum ./
RUN go mod download

COPY . .
# CGO_ENABLED=0: статический бинарь, ложится в distroless без libc-рантайма.
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/relay ./cmd/relay

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=builder /out/relay /relay
COPY --chown=nonroot:nonroot docs /docs
EXPOSE 8081
EXPOSE 8443/udp
ENTRYPOINT ["/relay"]
