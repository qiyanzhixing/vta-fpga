GO ?= go
BUILD_FLAGS := -trimpath -ldflags="-s -w"
.PHONY: test vet build build-native linux-amd64 linux-arm64 benchmark reference

test:
	$(GO) test -race ./...
vet:
	$(GO) vet ./...
build: linux-amd64 linux-arm64
linux-amd64:
	mkdir -p bin
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 $(GO) build $(BUILD_FLAGS) -o bin/vta-service-linux-amd64 ./cmd/vta-service
linux-arm64:
	mkdir -p bin
	CGO_ENABLED=0 GOOS=linux GOARCH=arm64 $(GO) build $(BUILD_FLAGS) -o bin/vta-service-linux-arm64 ./cmd/vta-service
build-native:
	mkdir -p bin
	$(GO) build $(BUILD_FLAGS) -o bin/vta-service ./cmd/vta-service
benchmark:
	$(GO) test ./internal/integration -run '^$$' -bench BenchmarkSample -benchmem
reference:
	.venv/bin/python tools/generate_reference_fixtures.py
	.venv/bin/python -m unittest discover -s tools -p test_reference_fixtures.py -v
