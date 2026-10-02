package heliosdb_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	heliosdb "github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go"
)

// newServer starts a fake HeliosDB REST endpoint that records the last
// request body and answers with the given JSON payload.
func newServer(t *testing.T, wantMethod, wantPath string, reply interface{}, gotBody *map[string]interface{}, gotKey *string) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != wantMethod || r.URL.Path != wantPath {
			t.Errorf("request = %s %s, want %s %s", r.Method, r.URL.Path, wantMethod, wantPath)
		}
		if gotKey != nil {
			*gotKey = r.Header.Get("X-API-Key")
		}
		if gotBody != nil && r.Body != nil {
			_ = json.NewDecoder(r.Body).Decode(gotBody)
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(reply)
	}))
	t.Cleanup(srv.Close)
	return srv
}

func TestQuerySendsSQLParamsAndAPIKey(t *testing.T) {
	var body map[string]interface{}
	var key string
	srv := newServer(t, http.MethodPost, "/v1/branches/main/query",
		map[string]interface{}{
			"columns": []string{"id", "name"},
			"rows":    []map[string]interface{}{{"id": 1, "name": "alice"}},
		}, &body, &key)

	c, err := heliosdb.NewClient(srv.URL, heliosdb.WithAPIKey("k1"))
	if err != nil {
		t.Fatal(err)
	}
	res, err := c.Query(context.Background(), "SELECT * FROM users WHERE id = $1", 1)
	if err != nil {
		t.Fatal(err)
	}
	if key != "k1" {
		t.Errorf("X-API-Key = %q, want k1", key)
	}
	if body["sql"] != "SELECT * FROM users WHERE id = $1" {
		t.Errorf("sql = %v", body["sql"])
	}
	if len(res.Rows) != 1 || res.Rows[0]["name"] != "alice" {
		t.Errorf("rows = %v", res.Rows)
	}
}

func TestQueryUsesConfiguredBranch(t *testing.T) {
	srv := newServer(t, http.MethodPost, "/v1/branches/dev/query",
		map[string]interface{}{"rows": []map[string]interface{}{}}, nil, nil)
	c, _ := heliosdb.NewClient(srv.URL, heliosdb.WithBranch("dev"))
	if c.GetBranch() != "dev" {
		t.Fatalf("GetBranch() = %q, want dev", c.GetBranch())
	}
	if _, err := c.Query(context.Background(), "SELECT 1"); err != nil {
		t.Fatal(err)
	}
}

func TestVectorSearchOptions(t *testing.T) {
	var body map[string]interface{}
	srv := newServer(t, http.MethodPost, "/v1/vectors/stores/docs/search/text",
		map[string]interface{}{
			"results": []map[string]interface{}{{"id": "a", "score": 0.9}},
		}, &body, nil)
	c, _ := heliosdb.NewClient(srv.URL)
	got, err := c.VectorSearch(context.Background(), "docs", "hello",
		heliosdb.WithTopK(3), heliosdb.WithMinScore(0.5))
	if err != nil {
		t.Fatal(err)
	}
	if body["top_k"] != float64(3) || body["min_score"] != 0.5 || body["text"] != "hello" {
		t.Errorf("body = %v", body)
	}
	if len(got) != 1 || got[0].ID != "a" {
		t.Errorf("results = %v", got)
	}
}

func TestListBranches(t *testing.T) {
	srv := newServer(t, http.MethodGet, "/v1/branches",
		map[string]interface{}{"branches": []map[string]interface{}{{"name": "main"}, {"name": "dev"}}}, nil, nil)
	c, _ := heliosdb.NewClient(srv.URL)
	b, err := c.ListBranches(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(b) != 2 || b[1].Name != "dev" {
		t.Errorf("branches = %v", b)
	}
}

func TestAgentMemoryAdd(t *testing.T) {
	var body map[string]interface{}
	srv := newServer(t, http.MethodPost, "/v1/agents/memory/s1/add", map[string]interface{}{}, &body, nil)
	c, _ := heliosdb.NewClient(srv.URL)
	if err := c.Memory("s1").Add(context.Background(), "user", "hi"); err != nil {
		t.Fatal(err)
	}
	if body["role"] != "user" || body["content"] != "hi" {
		t.Errorf("body = %v", body)
	}
}

func TestAPIErrorOnHTTPFailure(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "boom", http.StatusBadRequest)
	}))
	defer srv.Close()
	c, _ := heliosdb.NewClient(srv.URL)
	err := c.Health(context.Background())
	var apiErr *heliosdb.APIError
	if !errors.As(err, &apiErr) || apiErr.StatusCode != http.StatusBadRequest {
		t.Fatalf("err = %v, want *APIError with status 400", err)
	}
}
