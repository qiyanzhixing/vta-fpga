package protocol

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"strconv"
	"unicode/utf8"
)

// StrictJSON rejects duplicate keys at every depth, trailing values and invalid UTF-8.
func StrictJSON(ctx context.Context, raw []byte) (any, error) {
	if !utf8.Valid(raw) {
		return nil, fmt.Errorf("invalid UTF-8")
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	v, e := readValue(ctx, d, 0)
	if e != nil {
		return nil, e
	}
	if _, e = d.Token(); e != io.EOF {
		return nil, fmt.Errorf("trailing JSON")
	}
	return v, nil
}
func readValue(ctx context.Context, d *json.Decoder, depth int) (any, error) {
	if e := ctx.Err(); e != nil {
		return nil, e
	}
	if depth > 64 {
		return nil, fmt.Errorf("JSON nesting too deep")
	}
	t, e := d.Token()
	if e != nil {
		return nil, e
	}
	if delim, ok := t.(json.Delim); ok {
		switch delim {
		case '{':
			m := map[string]any{}
			for d.More() {
				k, e := d.Token()
				if e != nil {
					return nil, e
				}
				s, ok := k.(string)
				if !ok {
					return nil, fmt.Errorf("object key")
				}
				if _, ok = m[s]; ok {
					return nil, fmt.Errorf("duplicate key %q", s)
				}
				v, e := readValue(ctx, d, depth+1)
				if e != nil {
					return nil, e
				}
				m[s] = v
			}
			_, e = d.Token()
			return m, e
		case '[':
			a := []any{}
			for d.More() {
				v, e := readValue(ctx, d, depth+1)
				if e != nil {
					return nil, e
				}
				a = append(a, v)
			}
			_, e = d.Token()
			return a, e
		default:
			return nil, fmt.Errorf("unexpected delimiter")
		}
	}
	return t, nil
}
func object(v any, required []string, optional ...string) (map[string]any, bool) {
	m, ok := v.(map[string]any)
	if !ok {
		return nil, false
	}
	allowed := map[string]bool{}
	for _, k := range required {
		allowed[k] = true
		if _, ok := m[k]; !ok {
			return nil, false
		}
	}
	for _, k := range optional {
		allowed[k] = true
	}
	for k := range m {
		if !allowed[k] {
			return nil, false
		}
	}
	return m, true
}
func number(v any) (float64, bool) {
	n, ok := v.(json.Number)
	if !ok {
		return 0, false
	}
	f, e := FiniteNumber(n)
	return f, e == nil
}
func DecodeRequest(ctx context.Context, raw []byte, l Limits) (r Request, err *Error) {
	fail := func(code, field, msg string) (Request, *Error) { return r, Failure(r.RequestID, code, field, msg) }
	if int64(len(raw)) > l.MaxRequestBytes {
		return fail("LIMIT_EXCEEDED", "", "request byte budget exceeded")
	}
	v, e := StrictJSON(ctx, raw)
	if e != nil {
		if ctx.Err() != nil {
			return fail("PROCESSING_TIMEOUT", "", "processing canceled")
		}
		return fail("INVALID_JSON", "", e.Error())
	}
	if m, ok := v.(map[string]any); ok {
		r.RequestID, _ = m["request_id"].(string)
	}
	m, ok := object(v, []string{"version", "type", "request_id", "data", "config"})
	if !ok {
		return fail("INVALID_REQUEST", "", "invalid envelope fields")
	}
	n, ok := m["version"].(json.Number)
	if !ok {
		return fail("INVALID_REQUEST", "version", "integer required")
	}
	r.Version, e = strconv.Atoi(string(n))
	if e != nil {
		return fail("INVALID_REQUEST", "version", "integer required")
	}
	if r.Version != 1 {
		return fail("UNSUPPORTED_VERSION", "version", "supported version is 1")
	}
	r.Type, _ = m["type"].(string)
	if r.Type != "denoise" || !ValidID(r.RequestID) {
		return fail("INVALID_REQUEST", "", "invalid type or request_id")
	}
	dm, ok := object(m["data"], []string{"time_unit", "time", "channels", "intensity"})
	if !ok {
		return fail("INVALID_REQUEST", "data", "invalid data fields")
	}
	r.Data.TimeUnit, _ = dm["time_unit"].(string)
	if r.Data.TimeUnit != "min" {
		return fail("INVALID_DATA", "data.time_unit", "expected min")
	}
	ts, okT := dm["time"].([]any)
	cs, okC := dm["channels"].([]any)
	rows, okR := dm["intensity"].([]any)
	if !okT || !okC || !okR {
		return fail("INVALID_DATA", "data", "arrays required")
	}
	N, C := len(ts), len(cs)
	if N < 1 || C < 1 || len(rows) != N {
		return fail("INVALID_SHAPE", "data", "empty axes or mismatched rows")
	}
	if N > l.MaxTimePoints || C > l.MaxChannels || N > l.MaxCells/C {
		return fail("LIMIT_EXCEEDED", "data", "dimension budget exceeded")
	}
	r.Data.Time = make([]float64, N)
	r.Data.Channels = make([]string, C)
	r.Data.Intensity = make([][]float64, N)
	seen := map[string]bool{}
	for j, c := range cs {
		s, ok := c.(string)
		if !ok || len(s) == 0 || len(s) > 64 || s == "Time/min" || seen[s] {
			return fail("INVALID_DATA", "data.channels", "invalid or duplicate channel")
		}
		seen[s] = true
		r.Data.Channels[j] = s
	}
	for i, t := range ts {
		if ctx.Err() != nil {
			return fail("PROCESSING_TIMEOUT", "", "processing canceled")
		}
		f, ok := number(t)
		if !ok || f < 0 || (i > 0 && f <= r.Data.Time[i-1]) {
			return fail("INVALID_DATA", "data.time", "finite nonnegative increasing times required")
		}
		r.Data.Time[i] = f
		row, ok := rows[i].([]any)
		if !ok || len(row) != C {
			return fail("INVALID_SHAPE", fmt.Sprintf("data.intensity[%d]", i), "channel count mismatch")
		}
		r.Data.Intensity[i] = make([]float64, C)
		for j, val := range row {
			f, ok := number(val)
			if !ok {
				return fail("INVALID_DATA", fmt.Sprintf("data.intensity[%d][%d]", i, j), "finite number required")
			}
			r.Data.Intensity[i][j] = f
		}
	}
	cm, ok := object(m["config"], []string{"method"}, "params")
	if !ok {
		return fail("INVALID_REQUEST", "config", "invalid config fields")
	}
	r.Config.Method, ok = cm["method"].(string)
	if !ok || r.Config.Method == "" {
		return fail("INVALID_REQUEST", "config.method", "method string required")
	}
	p, exists := cm["params"]
	if !exists {
		p = map[string]any{}
	}
	if _, ok := p.(map[string]any); !ok {
		return fail("INVALID_CONFIG", "config.params", "object required")
	}
	r.Config.Params, _ = json.Marshal(p)
	return r, nil
}

// FiniteNumber rejects overflow and nonzero decimals that round down to zero.
// A lexical zero remains valid even with a very large exponent.
func FiniteNumber(n json.Number) (float64, error) {
	f, err := n.Float64()
	if err != nil || math.IsNaN(f) || math.IsInf(f, 0) {
		return 0, fmt.Errorf("number is outside float64 range")
	}
	if f == 0 {
		for _, ch := range string(n) {
			if ch == 'e' || ch == 'E' {
				break
			}
			if ch >= '1' && ch <= '9' {
				return 0, fmt.Errorf("nonzero number underflows float64")
			}
		}
	}
	return f, nil
}
