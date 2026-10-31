// Package protocol defines the version 1 JSON wire contract.
package protocol

import "encoding/json"

type Data struct {
	TimeUnit  string      `json:"time_unit"`
	Time      []float64   `json:"time"`
	Channels  []string    `json:"channels"`
	Intensity [][]float64 `json:"intensity"`
}
type Config struct {
	Method string          `json:"method"`
	Params json.RawMessage `json:"params"`
}
type Request struct {
	Version   int    `json:"version"`
	Type      string `json:"type"`
	RequestID string `json:"request_id"`
	Data      Data   `json:"data"`
	Config    Config `json:"config"`
}
type ErrorDetail struct {
	Code    string `json:"code"`
	Message string `json:"message"`
	Field   string `json:"field,omitempty"`
}
type Error struct {
	Version   int         `json:"version"`
	Type      string      `json:"type"`
	RequestID *string     `json:"request_id"`
	Detail    ErrorDetail `json:"error"`
}

func Failure(id, code, field, message string) *Error {
	var p *string
	if ValidID(id) {
		p = &id
	}
	return &Error{1, "error", p, ErrorDetail{code, message, field}}
}

type OSInfo struct {
	Name         *string `json:"name"`
	Distribution *string `json:"distribution"`
	Version      *string `json:"version"`
	Kernel       *string `json:"kernel"`
	Arch         *string `json:"arch"`
}
type DeviceInfo struct {
	ID    *string `json:"id"`
	Model *string `json:"model"`
	OS    OSInfo  `json:"os"`
}
type CPUSummary struct {
	Model        *string `json:"model"`
	LogicalCores *int    `json:"logical_cores"`
}
type MemorySummary struct {
	TotalBytes *uint64 `json:"total_bytes"`
}
type DeviceSummary struct {
	Device DeviceInfo    `json:"device"`
	CPU    CPUSummary    `json:"cpu"`
	Memory MemorySummary `json:"memory"`
}
type Hello struct {
	Version int    `json:"version"`
	Type    string `json:"type"`
	DeviceSummary
	Functions map[string][]string `json:"functions"`
}
type CPUInfo struct {
	CPUSummary
	UsagePercent   *float64 `json:"usage_percent"`
	SampleWindowMS *int64   `json:"sample_window_ms"`
}
type MemoryInfo struct {
	MemorySummary
	AvailableBytes *uint64  `json:"available_bytes"`
	UsedBytes      *uint64  `json:"used_bytes"`
	UsagePercent   *float64 `json:"usage_percent"`
}
type ServiceStatus struct {
	State          string `json:"state"`
	ActiveRequests int    `json:"active_requests"`
}
type CollectionError struct {
	Field   string `json:"field"`
	Code    string `json:"code"`
	Message string `json:"message,omitempty"`
}
type Heartbeat struct {
	Version          int               `json:"version"`
	Type             string            `json:"type"`
	ConnectionID     string            `json:"connection_id"`
	Seq              uint64            `json:"seq"`
	Timestamp        string            `json:"timestamp"`
	SampleAgeMS      int64             `json:"sample_age_ms"`
	UptimeSec        *float64          `json:"uptime_sec"`
	Device           DeviceInfo        `json:"device"`
	CPU              CPUInfo           `json:"cpu"`
	Memory           MemoryInfo        `json:"memory"`
	Service          ServiceStatus     `json:"service"`
	CollectionErrors []CollectionError `json:"collection_errors"`
}
type Calibration struct {
	TIdx [2]int  `json:"T_idx"`
	Lag  int     `json:"lag"`
	A    float64 `json:"a"`
	C    float64 `json:"c"`
}
type ColumnResult struct {
	Channel         string         `json:"channel"`
	Status          string         `json:"status"`
	RequestedMethod string         `json:"requested_method"`
	AppliedMethod   string         `json:"applied_method"`
	EffectiveParams map[string]any `json:"effective_params"`
	Code            string         `json:"code"`
	Message         string         `json:"message,omitempty"`
	Calibration     *Calibration   `json:"calibration,omitempty"`
}
type Stats struct {
	Total     int `json:"total"`
	Processed int `json:"processed"`
	Skipped   int `json:"skipped"`
	Errors    int `json:"errors"`
}
type Timing struct {
	ProcessingMS float64 `json:"processing_ms"`
}
type Result struct {
	Version          int            `json:"version"`
	Type             string         `json:"type"`
	RequestID        string         `json:"request_id"`
	Status           string         `json:"status"`
	AlgorithmVersion string         `json:"algorithm_version"`
	Data             Data           `json:"data"`
	Stats            Stats          `json:"stats"`
	ColumnResults    []ColumnResult `json:"column_results"`
	Timing           Timing         `json:"timing"`
}
