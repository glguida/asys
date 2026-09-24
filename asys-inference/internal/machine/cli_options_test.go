package machine

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestRootSelectionAtEveryCommandBoundary(t *testing.T) {
	for _, args := range [][]string{
		{"--root", "/selected", "show", "--json"},
		{"show", "--root", "/selected", "--json"},
		{"show", "--json", "--root=/selected"},
	} {
		root, rest, err := extractRoot(args)
		if err != nil || root != "/selected" || !reflect.DeepEqual(rest, []string{"show", "--json"}) {
			t.Fatalf("%v: %q %v %v", args, root, rest, err)
		}
	}
	root, rest, err := extractRoot([]string{"add", "custom", "source", "--root", "/host", "--", "--root", "/component"})
	if err != nil || root != "/host" || !reflect.DeepEqual(rest, []string{"add", "custom", "source", "--", "--root", "/component"}) {
		t.Fatalf("forwarded arguments changed: %q %v %v", root, rest, err)
	}
	for _, args := range [][]string{{"show", "--root"}, {"--root="}, {"--root", "--help"}} {
		if _, _, err := extractRoot(args); err == nil {
			t.Fatalf("accepted %v", args)
		}
	}
}

func TestCommandHelpNeedsNoConfiguredStateOrRuntime(t *testing.T) {
	root := filepath.Join(t.TempDir(), "absent")
	for _, command := range []string{"init", "start", "stop", "add", "remove", "select", "components", "show", "status", "models", "gateway"} {
		var out, stderr bytes.Buffer
		cli := CLI{Out: &out, Err: &stderr}
		if code := cli.Run(context.Background(), []string{command, "--root", root, "--help"}); code != 0 || !strings.Contains(out.String(), "Usage: asys-inference "+command) {
			t.Fatalf("%s: code=%d, %s %s", command, code, out.String(), stderr.String())
		}
	}
	if _, err := os.Stat(root); !os.IsNotExist(err) {
		t.Fatal("help created state")
	}
}

func TestRootEnvironmentPrecedenceAndHomeExpansion(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("ASYS_STATE_ROOT", "~/chosen")
	t.Setenv("XDG_STATE_HOME", filepath.Join(home, "xdg"))
	for _, item := range []struct{ override, want string }{
		{"~/chosen", filepath.Join(home, "chosen")},
		{"", filepath.Join(home, "xdg/asys")},
	} {
		t.Setenv("ASYS_STATE_ROOT", item.override)
		got, err := DefaultRoot()
		if err != nil || got != item.want {
			t.Fatalf("%q %v", got, err)
		}
	}
}
