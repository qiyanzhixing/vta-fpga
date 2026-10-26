package denoise

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"vta-fpga/internal/protocol"
)

var defaults = map[string]map[string]any{
	"none": {}, "mean": {"window_size": 5}, "std_threshold": {"window_size": 11, "sigma_multiple": 3.}, "ewma": {"alpha": .3}, "median": {"window_size": 5}, "quantile": {"window_size": 11, "quantile": .75}, "kalman": {"process_variance": 1e-5, "measurement_variance": .1},
}

func clone(p map[string]any) map[string]any {
	q := map[string]any{}
	for k, v := range p {
		q[k] = v
	}
	return q
}
func (r *Registry) Parse(req protocol.Request, maxWindow int) (Config, *protocol.Error) {
	c := Config{req.Config.Method, nil}
	fail := func(code, field, msg string) (Config, *protocol.Error) {
		return c, protocol.Failure(req.RequestID, code, "config.params"+field, msg)
	}
	d, ok := defaults[c.Method]
	if !ok || !r.Has(c.Method) {
		return fail("UNSUPPORTED_METHOD", "", "unsupported operator")
	}
	raw := req.Config.Params
	if len(raw) == 0 {
		raw = []byte("{}")
	}
	v, e := protocol.StrictJSON(context.Background(), raw)
	if e != nil {
		return fail("INVALID_CONFIG", "", e.Error())
	}
	p, ok := v.(map[string]any)
	if !ok {
		return fail("INVALID_CONFIG", "", "object required")
	}
	c.Params = clone(d)
	for k, v := range p {
		def, ok := d[k]
		if !ok {
			return fail("INVALID_CONFIG", "."+k, "unknown parameter")
		}
		if v == nil {
			return fail("INVALID_CONFIG", "."+k, "null is not allowed")
		}
		switch def.(type) {
		case int:
			n, ok := v.(json.Number)
			if !ok {
				return fail("INVALID_CONFIG", "."+k, "integer required")
			}
			i, e := strconv.Atoi(string(n))
			if e != nil {
				return fail("INVALID_CONFIG", "."+k, "lexical integer required")
			}
			c.Params[k] = i
		case float64:
			n, ok := v.(json.Number)
			if !ok {
				return fail("INVALID_CONFIG", "."+k, "number required")
			}
			f, e := n.Float64()
			if e != nil || math.IsInf(f, 0) || math.IsNaN(f) {
				return fail("INVALID_CONFIG", "."+k, "finite number required")
			}
			c.Params[k] = f
		case string:
			s, ok := v.(string)
			if !ok {
				return fail("INVALID_CONFIG", "."+k, "string required")
			}
			c.Params[k] = s
		case []string:
			a, ok := v.([]any)
			if !ok {
				return fail("INVALID_CONFIG", "."+k, "string array required")
			}
			ss := make([]string, len(a))
			for i, val := range a {
				s, ok := val.(string)
				if !ok {
					return fail("INVALID_CONFIG", "."+k, "string array required")
				}
				ss[i] = s
			}
			c.Params[k] = ss
		case []int:
			a, ok := v.([]any)
			if !ok || len(a) != 2 {
				return fail("INVALID_CONFIG", "."+k, "two integers required")
			}
			is := make([]int, 2)
			for i, val := range a {
				n, ok := val.(json.Number)
				if !ok {
					return fail("INVALID_CONFIG", "."+k, "integer required")
				}
				z, e := strconv.Atoi(string(n))
				if e != nil {
					return fail("INVALID_CONFIG", "."+k, "integer required")
				}
				is[i] = z
			}
			c.Params[k] = is
		}
	}
	if w, ok := c.Params["window_size"].(int); ok {
		if w < 1 || w > maxWindow || (c.Method == "std_threshold" || c.Method == "savitzky_golay") && w%2 == 0 {
			return fail("INVALID_CONFIG", ".window_size", "invalid window")
		}
	}
	for _, k := range []string{"sigma_multiple", "process_variance"} {
		if v, ok := c.Params[k].(float64); ok && v < 0 {
			return fail("INVALID_CONFIG", "."+k, "must be nonnegative")
		}
	}
	for _, k := range []string{"alpha", "T_frac"} {
		if v, ok := c.Params[k].(float64); ok && (v <= 0 || v > 1) {
			return fail("INVALID_CONFIG", "."+k, "must be in (0,1]")
		}
	}
	if v, ok := c.Params["quantile"].(float64); ok && (v < 0 || v > 1) {
		return fail("INVALID_CONFIG", ".quantile", "must be in [0,1]")
	}
	if v, ok := c.Params["measurement_variance"].(float64); ok && v <= 0 {
		return fail("INVALID_CONFIG", ".measurement_variance", "must be positive")
	}
	if c.Method == "savitzky_golay" {
		o := c.Params["polyorder"].(int)
		if o < 0 || o >= c.Params["window_size"].(int) {
			return fail("INVALID_CONFIG", ".polyorder", "must be smaller than window")
		}
	}
	if c.Method == "wavelet" {
		if c.Params["wavelet"] != "db4" {
			return fail("UNSUPPORTED_METHOD", ".wavelet", "only db4 is supported")
		}
		lv := c.Params["level"].(int)
		if lv < 1 || lv > 16 {
			return fail("INVALID_CONFIG", ".level", "must be 1..16")
		}
		if c.Params["threshold_type"] != "soft" && c.Params["threshold_type"] != "hard" {
			return fail("INVALID_CONFIG", ".threshold_type", "must be soft or hard")
		}
	}
	if c.Method == "mz5_11" {
		if e := baselineParams(req.Data, &c, p, maxWindow); e != nil {
			return fail("INVALID_CONFIG", "", e.Error())
		}
	}
	return c, nil
}

// Populated when the matrix operator is registered.
var baselineParams = func(d protocol.Data, c *Config, p map[string]any, maxWindow int) error {
	return fmt.Errorf("matrix operator unavailable")
}
