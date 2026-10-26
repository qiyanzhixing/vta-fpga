package protocol

import "time"

type Limits struct {
	MaxRequestBytes, MaxResponseBytes                                                                                int64
	MaxTimePoints, MaxChannels, MaxCells, MaxWindowSize, MaxInflightPerConnection, MaxActiveRequests, MaxConnections int
	ProcessingTimeout, UploadTimeout, WriteTimeout, PingInterval, PongTimeout, ReportInterval, ReportStaleTimeout    time.Duration
}

func DefaultLimits() Limits {
	return Limits{16777216, 33554432, 100000, 1024, 500000, 4095, 1, 1, 4, 120 * time.Second, 30 * time.Second, 10 * time.Second, 20 * time.Second, 60 * time.Second, 5 * time.Second, 15 * time.Second}
}
func ValidID(s string) bool {
	if len(s) < 1 || len(s) > 64 {
		return false
	}
	for _, c := range []byte(s) {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_' || c == '-') {
			return false
		}
	}
	return true
}
