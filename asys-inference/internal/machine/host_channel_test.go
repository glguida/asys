package machine

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestHostChannelsAreDeclaredBySourcesAndIsolatedPerProvider(t *testing.T) {
	root := t.TempDir()
	source := filepath.Join(root, "arbitrary_source")
	if err := os.Mkdir(source, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(source, "runtime.json"), []byte(`{"host_channel":"gateway","external_egress":true}`), 0600); err != nil {
		t.Fatal(err)
	}
	runtime := &Runtime{Store: Store{Root: filepath.Join(root, "machine")}}
	config := Config{ComponentsRoot: root}
	first, err := runtime.hostRuntime(config, Provider{Name: "first", Source: "arbitrary_source"})
	if err != nil {
		t.Fatal(err)
	}
	second, err := runtime.hostRuntime(config, Provider{Name: "second", Source: "arbitrary_source"})
	if err != nil {
		t.Fatal(err)
	}
	if len(first.Binds) != 1 || len(second.Binds) != 1 || first.Binds[0].Source == second.Binds[0].Source || first.Binds[0].Target != hostRootTarget {
		t.Fatalf("channel mounts are not isolated: %+v %+v", first, second)
	}
	identity, err := os.ReadFile(filepath.Join(first.Binds[0].Source, "provider.json"))
	if err != nil {
		t.Fatal(err)
	}
	var name struct {
		Version int    `json:"version"`
		Name    string `json:"name"`
	}
	if err := json.Unmarshal(identity, &name); err != nil || name.Version != 1 || name.Name != "first" {
		t.Fatalf("logical provider identity missing: %s %v", identity, err)
	}
	for _, item := range []struct {
		path string
		mode os.FileMode
	}{
		{filepath.Join(runtime.Store.Root, "host"), 0700},
		{first.Binds[0].Source, 0777},
		{filepath.Join(first.Binds[0].Source, "provider.json"), 0644},
		{filepath.Join(first.Binds[0].Source, "channels/gateway/in"), 0777},
		{filepath.Join(first.Binds[0].Source, "channels/gateway/out"), 0777},
	} {
		info, err := os.Stat(item.path)
		if err != nil || info.Mode().Perm() != item.mode {
			t.Fatalf("%s: expected %o: %v %+v", item.path, item.mode, err, info)
		}
	}
	defaults, err := config.RuntimeDefaults(Provider{Source: "arbitrary_source"})
	if err != nil || !defaults.ExternalEgress || len(defaults.Binds) != 0 {
		t.Fatalf("host metadata leaked into dcomp defaults: %+v %v", defaults, err)
	}
}

func TestAComponentWithoutHostChannelGetsNoHostMount(t *testing.T) {
	root := t.TempDir()
	runtime := &Runtime{Store: Store{Root: filepath.Join(root, "machine")}}
	result, err := runtime.hostRuntime(Config{ComponentsRoot: root}, Provider{Name: "filter", Source: root})
	if err != nil || len(result.Binds) != 0 {
		t.Fatalf("unexpected host channel: %+v %v", result, err)
	}
	if _, err := os.Stat(filepath.Join(runtime.Store.Root, "host")); !os.IsNotExist(err) {
		t.Fatal("created unused host root")
	}
}
