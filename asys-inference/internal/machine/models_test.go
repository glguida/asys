package machine

import (
	"bytes"
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/glguida/dcomp/composition"
)

func TestModelsIsAReadCommand(t *testing.T) {
	root := t.TempDir()
	document, _ := NewDocument(Config{System: "test", DCompRoot: filepath.Join(root, "dcomp")})
	store := Store{Root: filepath.Join(root, "inference")}
	if err := os.MkdirAll(store.Root, 0700); err != nil {
		t.Fatal(err)
	}
	if err := store.Write(document); err != nil {
		t.Fatal(err)
	}
	var stderr bytes.Buffer
	cli := CLI{Out: io.Discard, Err: &stderr, NewRuntime: func(Store, Config, io.Writer) (*Runtime, error) {
		return nil, errors.New("catalogue runtime reached")
	}}
	if code := cli.Run(context.Background(), []string{"--root", root, "models", "--json"}); code != 1 || !strings.Contains(stderr.String(), "catalogue runtime reached") {
		t.Fatalf("models command was not dispatched: %s", &stderr)
	}
}

func TestModelsResolveTheServingGlobalInsteadOfTheRequestedEndpoint(t *testing.T) {
	config, status := readinessFixture()
	document := Document{Config: config}
	status.Spec.Components = []composition.ResolvedComponent{
		{Name: "provider-root", Definition: producer("root").Component.Definition},
		{Name: "provider-wrapper", Definition: producer("wrapper").Component.Definition},
	}
	status.Spec.Globals = []composition.Global{global("provider-root")}
	name, err := modelsTarget(&document, status)
	if err != nil || name != "root" {
		t.Fatalf("did not resolve the serving output: %q %v", name, err)
	}
	status.Spec.Globals[0] = global("provider-wrapper")
	if name, err = modelsTarget(&document, status); err != nil || name != "wrapper" {
		t.Fatalf("did not follow global reassignment: %q %v", name, err)
	}
	status.Spec.Globals[0].Target.Endpoint = "other"
	if _, err = modelsTarget(&document, status); err == nil {
		t.Fatal("accepted a different component interface")
	}
	status.Spec.Globals[0].Target = composition.EndpointRef{}
	if _, err = modelsTarget(&document, status); err == nil || !strings.Contains(err.Error(), "unbound") {
		t.Fatalf("missing unbound diagnostic: %v", err)
	}
}
