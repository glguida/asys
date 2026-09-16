package machine

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/glguida/dcomp/composition"
	"github.com/glguida/dcomp/lifecycle"
	"github.com/glguida/dcomp/proxy"
	"github.com/glguida/dcomp/state"
)

func TestAddPassesAllArgumentsExceptLinks(t *testing.T) {
	components, err := filepath.Abs("../../components")
	if err != nil {
		t.Fatal(err)
	}
	variants := [][]string{
		{"-L", "upstream=gateway.provider", "pool", "pooler", "work", "personal"},
		{"pool", "--link", "upstream=gateway.provider", "pooler", "work", "personal"},
		{"pool", "pooler", "-L", "upstream=gateway.provider", "work", "personal"},
		{"pool", "pooler", "work", "--link", "upstream=gateway.provider", "personal"},
		{"pool", "pooler", "work", "personal", "--link=upstream=gateway.provider"},
		{"pool", "pooler", "work", "personal", "-L=upstream=gateway.provider"},
	}
	cli := CLI{Out: io.Discard, Err: io.Discard}
	for _, args := range variants {
		config := Config{ComponentsRoot: components}
		if err := cli.add(&config, args); err != nil {
			t.Fatalf("%v: %v", args, err)
		}
		p := config.Providers[0]
		if p.Name != "pool" || p.Source != "pooler" || p.Links["upstream"] != "gateway.provider" || !reflect.DeepEqual(p.Runtime.Args, []string{"work", "personal"}) || config.Endpoint != "pool.provider" {
			t.Fatalf("%v: unexpected config %+v", args, config)
		}
	}
	config := Config{ComponentsRoot: components}
	literal := []string{"--arg", "--context", "--no-select", "--egress", "--help", "-x", "", "--", "model=balanced"}
	if err := cli.add(&config, append([]string{"trace", "passthrough"}, literal...)); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(config.Providers[0].Runtime.Args, literal) || config.Providers[0].Runtime.ExternalEgress {
		t.Fatal("component arguments were interpreted as tool options")
	}
}

func TestAddRejectsIncompleteOrDuplicateLinks(t *testing.T) {
	cli := CLI{Out: io.Discard, Err: io.Discard}
	for _, args := range [][]string{
		{"name"},
		{"-L", "upstream=gateway.provider", "name"},
		{"--link", "upstream=gateway.provider"},
		{"name", "passthrough", "-L"},
		{"name", "passthrough", "--link", "upstream"},
		{"name", "passthrough", "-L", "=gateway.provider"},
		{"name", "passthrough", "-L", "upstream="},
		{"name", "passthrough", "-L", "upstream=a.provider", "--link", "upstream=b.provider"},
	} {
		config := Config{}
		if err := cli.add(&config, args); err == nil {
			t.Fatalf("accepted %v", args)
		}
		if len(config.Providers) != 0 {
			t.Fatal("invalid add changed configuration")
		}
	}
}

func TestArbitraryComponentDirectoryAndDefaults(t *testing.T) {
	root := t.TempDir()
	source := filepath.Join(root, "custom_filter")
	if err := os.Mkdir(source, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(source, "component.dcomp"), []byte("docker custom:local\noutput "+Service+" result\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(source, "runtime.json"), []byte(`{"external_egress":true,"args":["default"]}`), 0600); err != nil {
		t.Fatal(err)
	}
	cli := CLI{Out: io.Discard, Err: io.Discard}
	for _, input := range []string{"custom_filter", source, filepath.Join(source, "component.dcomp")} {
		config := Config{ComponentsRoot: root}
		if err := cli.add(&config, []string{"filter", input}); err != nil {
			t.Fatal(err)
		}
		p := config.Providers[0]
		resolved, context, err := config.Source(p)
		if err != nil || resolved != source || config.Endpoint != "filter.result" || !p.Runtime.ExternalEgress || !reflect.DeepEqual(p.Runtime.Args, []string{"default"}) {
			t.Fatalf("source %s: %+v, %s, %v", input, p, resolved, err)
		}
		if input == "custom_filter" && context != root {
			t.Fatal("named component lost shared build context")
		}
	}
	config := Config{ComponentsRoot: root}
	if err := cli.add(&config, []string{"filter", "custom_filter", "override", "--flag"}); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(config.Providers[0].Runtime.Args, []string{"override", "--flag"}) {
		t.Fatal("explicit arguments did not replace defaults")
	}
}

func TestDefaultRoot(t *testing.T) {
	t.Setenv("ASYS_INFERENCE_STATE_ROOT", "")
	t.Setenv("ASYS_STATE_ROOT", "")
	base := t.TempDir()
	t.Setenv("XDG_STATE_HOME", base)
	if root, err := DefaultRoot(); err != nil || root != filepath.Join(base, "asys/inference") {
		t.Fatalf("default root %q: %v", root, err)
	}
	t.Setenv("ASYS_STATE_ROOT", filepath.Join(base, "shared"))
	if root, err := DefaultRoot(); err != nil || root != filepath.Join(base, "shared/inference") {
		t.Fatalf("shared root %q: %v", root, err)
	}
	t.Setenv("ASYS_INFERENCE_STATE_ROOT", filepath.Join(base, "custom"))
	if root, err := DefaultRoot(); err != nil || root != filepath.Join(base, "custom") {
		t.Fatalf("configured root %q: %v", root, err)
	}
}

func TestComponentsListsDirectoryWithoutMachineOrDocker(t *testing.T) {
	root := t.TempDir()
	source := filepath.Join(root, "custom")
	if err := os.Mkdir(source, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(source, "component.dcomp"), []byte("docker custom:local\noutput "+Service+" result\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "README.md"), []byte("components"), 0600); err != nil {
		t.Fatal(err)
	}
	var out, stderr bytes.Buffer
	cli := CLI{Out: &out, Err: &stderr}
	code := cli.Run(context.Background(), []string{"--root", filepath.Join(root, "state"), "components", "--components-root", root, "--json"})
	if code != 0 {
		t.Fatalf("components %d: %s", code, stderr.String())
	}
	var entries []componentEntry
	if err := json.Unmarshal(out.Bytes(), &entries); err != nil || len(entries) != 1 || entries[0].Name != "custom" || entries[0].Definition.Outputs[0].Name != "result" {
		t.Fatalf("unexpected component list %s: %v", out.String(), err)
	}
	if _, err := os.Stat(filepath.Join(root, "state")); !os.IsNotExist(err) {
		t.Fatal("listing components created machine state")
	}
}

func TestGenericCommandsAreNotPublicAndDoNotCreateState(t *testing.T) {
	root := filepath.Join(t.TempDir(), "state")
	var out, stderr bytes.Buffer
	cli := CLI{Out: &out, Err: &stderr}
	for _, command := range []string{"configure", "sync", "wire", "query"} {
		stderr.Reset()
		if code := cli.Run(context.Background(), []string{"--root", root, command}); code != 2 || !strings.Contains(stderr.String(), "unknown command") {
			t.Fatalf("%s: code=%d, %s", command, code, stderr.String())
		}
	}
	if _, err := os.Stat(root); !os.IsNotExist(err) {
		t.Fatal("unknown command created state")
	}
}

func TestInterspersedOptionsDelimiterAndErrors(t *testing.T) {
	for _, tc := range []struct {
		args []string
		want []string
		fail bool
	}{
		{args: []string{"name", "--arg", "--root", "--", "--literal"}, want: []string{"name", "--literal"}},
		{args: []string{"name", "--unknown"}, fail: true},
		{args: []string{"name", "--arg"}, fail: true},
	} {
		flags := flag.NewFlagSet("test", flag.ContinueOnError)
		flags.SetOutput(io.Discard)
		value := flags.String("arg", "", "literal")
		err := parseOptions(flags, tc.args)
		if (err != nil) != tc.fail {
			t.Fatalf("%v: %v", tc.args, err)
		}
		if !tc.fail && (!reflect.DeepEqual(flags.Args(), tc.want) || *value != "--root") {
			t.Fatalf("literal arguments changed: %v, %q", flags.Args(), *value)
		}
	}
}

func producer(name string) composition.Instance {
	return composition.Instance{Name: name, Component: composition.Component{Image: "sha256:provider", Definition: composition.Definition{Outputs: []composition.Endpoint{{Name: "provider", Service: Service}}}}}
}
func consumer(name string) composition.Instance {
	return composition.Instance{Name: name, Component: composition.Component{Image: "sha256:consumer", Definition: composition.Definition{Inputs: []composition.Endpoint{{Name: "inference", Service: Service}}}}}
}
func ref(component, endpoint string) composition.EndpointRef {
	return composition.EndpointRef{Component: component, Endpoint: endpoint}
}
func global(target string) composition.Global {
	g := composition.Global{Name: GlobalName, Service: Service}
	if target != "" {
		g.Target = ref(target, "provider")
	}
	return g
}
func wire(name, target string) composition.Link {
	return composition.Link{Input: ref(name, "inference"), Output: ref(target, "provider")}
}
func symbolic(name string) composition.Link {
	return composition.Link{Input: ref(name, "inference"), Output: composition.EndpointRef{Global: GlobalName}}
}

func TestMergePreservesIndependentComponentsAndSymbolicConsumers(t *testing.T) {
	root := producer("inference-root")
	relay := producer("inference-relay")
	relay.Component.Definition.Inputs = []composition.Endpoint{{Name: "inference", Service: Service}}
	team := consumer("team")
	fixed := consumer("fixed")
	other := producer("foreign")
	before := Bundle{Components: []composition.Instance{root}, Global: global(root.Name)}
	current := composition.Spec{Name: "asys", Components: []composition.Instance{root, team, fixed, other}, Links: []composition.Link{symbolic("team"), wire("fixed", root.Name)}, Globals: []composition.Global{before.Global, {Name: "other_endpoint", Service: Service, Target: ref("foreign", "provider")}}}
	target := Bundle{Components: []composition.Instance{root, relay}, Links: []composition.Link{wire(relay.Name, root.Name)}, Global: global(relay.Name)}
	if err := Merge(&current, &before, target); err != nil {
		t.Fatal(err)
	}
	if len(current.Components) != 5 || len(current.Links) != 3 {
		t.Fatalf("lost peers: %+v", current)
	}
	if current.Links[0] != symbolic("team") || current.Links[1] != wire("fixed", root.Name) {
		t.Fatal("external wires changed")
	}
	if current.Globals[0].Name != "other_endpoint" || current.Globals[0].Target.Component != "foreign" {
		t.Fatal("foreign global changed")
	}
	if !matches(current, target, &before) {
		t.Fatal("applied contribution does not match")
	}
}

func TestStopUnbindsButRetainsConsumersAndOtherContributors(t *testing.T) {
	root := producer("inference-root")
	before := Bundle{Components: []composition.Instance{root}, Global: global(root.Name)}
	current := composition.Spec{Name: "asys", Components: []composition.Instance{root, consumer("team"), consumer("fixed"), producer("foreign")}, Links: []composition.Link{symbolic("team"), wire("fixed", root.Name)}, Globals: []composition.Global{before.Global, {Name: "alias", Service: Service, Target: ref(root.Name, "provider")}}}
	target := Bundle{Global: global("")}
	if err := Merge(&current, &before, target); err != nil {
		t.Fatal(err)
	}
	if len(current.Components) != 3 || len(current.Links) != 1 || current.Links[0] != symbolic("team") {
		t.Fatalf("incorrect stop: %+v", current)
	}
	for _, g := range current.Globals {
		if g.Target != (composition.EndpointRef{}) {
			t.Fatal("removed output still bound")
		}
	}
}

func TestMergeRefusesForeignNamesAndGlobalAssignments(t *testing.T) {
	target := Bundle{Components: []composition.Instance{producer("inference-root")}, Global: global("inference-root")}
	for _, current := range []composition.Spec{
		{Name: "asys", Components: []composition.Instance{producer("inference-root")}},
		{Name: "asys", Components: []composition.Instance{producer("foreign")}, Globals: []composition.Global{global("foreign")}},
	} {
		original, _ := json.Marshal(current)
		if err := Merge(&current, nil, target); err == nil {
			t.Fatal("claimed foreign resources")
		}
		after, _ := json.Marshal(current)
		if !bytes.Equal(original, after) {
			t.Fatal("failed merge mutated current state")
		}
	}
}

func TestReconfigureOwnedProviderAndPreserveUnrelatedRuntime(t *testing.T) {
	old := producer("inference-root")
	old.Runtime.Args = []string{"before"}
	next := old
	next.Runtime.Args = []string{"after"}
	foreign := producer("foreign")
	foreign.Runtime.User = "1001:1001"
	foreign.Runtime.Args = []string{"keep"}
	foreign.Runtime.Binds = []composition.BindMount{{Source: filepath.Join(t.TempDir(), "moved-workspace"), Target: "/workspace"}}
	before := Bundle{Components: []composition.Instance{old}, Global: global(old.Name)}
	current := composition.Spec{Name: "asys", Components: []composition.Instance{old, foreign}, Globals: []composition.Global{before.Global}}
	if err := Merge(&current, &before, Bundle{Components: []composition.Instance{next}, Global: before.Global}); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(current.Components[0], foreign) || !reflect.DeepEqual(current.Components[1].Runtime.Args, []string{"after"}) {
		t.Fatal("wrong replacement")
	}
	// An independent immutable change to our component must not be silently overwritten.
	current.Components[1].Runtime.Args = []string{"external"}
	if err := Merge(&current, &before, before); err == nil {
		t.Fatal("overwrote external component change")
	}
}

func TestStoreSerializesConcurrentConfigurationCommands(t *testing.T) {
	store := Store{Root: t.TempDir()}
	doc, _ := NewDocument(Config{})
	if err := store.Write(doc); err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	errs := make(chan error, 16)
	for range 16 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			unlock, err := store.Lock(context.Background())
			if err != nil {
				errs <- err
				return
			}
			defer unlock()
			d, err := store.Read()
			if err != nil {
				errs <- err
				return
			}
			d.Revision++
			errs <- store.Write(d)
		}()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	result, err := store.Read()
	if err != nil {
		t.Fatal(err)
	}
	if result.Revision != 17 {
		t.Fatalf("lost edit: revision %d", result.Revision)
	}
	unlock, err := store.Lock(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	defer unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if _, err := store.Lock(ctx); err == nil {
		t.Fatal("lock ignored cancellation")
	}
}

func TestRecoverLandedApplyWithLaterForeignAddition(t *testing.T) {
	store := Store{Root: t.TempDir()}
	droot := t.TempDir()
	root := producer("inference-root")
	peer := producer("peer")
	peer.Runtime.User = "1001:1001"
	workspace := filepath.Join(t.TempDir(), "workspace")
	if err := os.Mkdir(workspace, 0700); err != nil {
		t.Fatal(err)
	}
	peer.Runtime.Binds = []composition.BindMount{{Source: workspace, Target: "/workspace"}}
	target := Bundle{Components: []composition.Instance{root}, Global: global(root.Name)}
	doc, _ := NewDocument(Config{System: "asys", DCompRoot: droot, RuntimeRoot: filepath.Join(droot, "run")})
	doc.Pending = &Pending{Digest: "unused-after-commit", Revision: doc.Revision, Bundle: target}
	if err := store.Write(doc); err != nil {
		t.Fatal(err)
	}
	current := composition.Spec{Name: "asys", Components: []composition.Instance{root, peer}, Globals: []composition.Global{target.Global}}
	images := map[string]composition.ResolvedImage{}
	for _, c := range current.Components {
		images[c.Name] = composition.ResolvedImage{ID: c.Component.Image, HasHealthcheck: true}
	}
	resolved, err := composition.Resolve(current, images)
	if err != nil {
		t.Fatal(err)
	}
	controller := &lifecycle.Controller{State: state.Store{Root: droot}}
	lock, err := controller.State.AcquireContext(context.Background(), "asys")
	if err != nil {
		t.Fatal(err)
	}
	runtimeDir := filepath.Join(doc.Config.RuntimeRoot, "asys")
	err = controller.State.WriteDesired("asys", state.Deployment{Spec: resolved, RuntimeRoot: doc.Config.RuntimeRoot, Containers: map[string]state.Resource{}, Networks: map[string]state.Resource{}, Proxy: &proxy.Process{InstanceID: "test", PID: 1, Digest: "test", RuntimeDir: runtimeDir, Control: proxy.ControlSocket(runtimeDir), Log: filepath.Join(runtimeDir, proxy.LogFileName)}})
	lock.Close()
	if err != nil {
		t.Fatal(err)
	}
	if err = os.Rename(workspace, workspace+"-moved"); err != nil {
		t.Fatal(err)
	}
	runtime := Runtime{Store: store, Controller: controller}
	if err = runtime.Recover(context.Background(), doc); err != nil {
		t.Fatal(err)
	}
	if doc.Pending != nil || doc.AppliedRevision != doc.Revision || doc.Applied == nil {
		t.Fatal("did not acknowledge landed apply")
	}
}

func TestRecoverDoesNotResumeAnotherContributorsOperation(t *testing.T) {
	store := Store{Root: t.TempDir()}
	droot := t.TempDir()
	doc, _ := NewDocument(Config{System: "asys", DCompRoot: droot, RuntimeRoot: filepath.Join(droot, "run")})
	controller := &lifecycle.Controller{State: state.Store{Root: droot}}
	lock, err := controller.State.AcquireContext(context.Background(), "asys")
	if err != nil {
		t.Fatal(err)
	}
	target, err := composition.Resolve(composition.Spec{Name: "asys"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	op, err := state.NewOperation("apply", "retire", target, nil, doc.Config.RuntimeRoot)
	if err != nil {
		t.Fatal(err)
	}
	err = controller.State.WriteOperation("asys", op)
	lock.Close()
	if err != nil {
		t.Fatal(err)
	}
	runtime := Runtime{Store: store, Controller: controller}
	if err = runtime.Recover(context.Background(), doc); err == nil || !strings.Contains(err.Error(), "another pending") {
		t.Fatalf("unexpected error: %v", err)
	}
	actual, exists, err := controller.State.ReadOperation("asys")
	if err != nil || !exists || actual.ID != op.ID {
		t.Fatal("changed peer operation")
	}
}

func TestInitAndShowNeedNoDocker(t *testing.T) {
	root := t.TempDir()
	components, err := filepath.Abs("../../components")
	if err != nil {
		t.Fatal(err)
	}
	var out, stderr bytes.Buffer
	cli := CLI{Out: &out, Err: &stderr}
	args := []string{"--root", root, "init", "--components-root", components, "--dcomp-state-root", filepath.Join(root, "dcomp")}
	if code := cli.Run(context.Background(), args); code != 0 {
		t.Fatalf("init %d: %s", code, &stderr)
	}
	out.Reset()
	if code := cli.Run(context.Background(), []string{"--root", root, "show", "--json"}); code != 0 {
		t.Fatalf("show %d: %s", code, &stderr)
	}
	var config Config
	if err := json.Unmarshal(out.Bytes(), &config); err != nil || config.Endpoint != "gateway.provider" {
		t.Fatalf("unexpected configuration %s: %v", out.String(), err)
	}
	if _, err := os.Stat(filepath.Join(root, "dcomp")); !os.IsNotExist(err) {
		t.Fatal("init touched dcomp runtime")
	}
	before, _ := os.ReadFile(filepath.Join(root, "machine.json"))
	out.Reset()
	cli.Run(context.Background(), []string{"--root", root, "show", "--json"})
	after, _ := os.ReadFile(filepath.Join(root, "machine.json"))
	if !bytes.Equal(before, after) {
		t.Fatal("show mutated config")
	}
}
