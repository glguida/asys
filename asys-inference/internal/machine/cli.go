package machine

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"text/tabwriter"

	"github.com/glguida/dcomp/proxy"
	"github.com/glguida/dcomp/state"
)

const Usage = `asys-inference manages a provider network exporting @inference_endpoint.

Usage: asys-inference [--root DIR] COMMAND [ARGUMENTS] [OPTIONS]

  init [--system NAME] [--dcomp-state-root DIR] [--runtime-root DIR]
       [--prefix NAME] [--components-root DIR] [--empty]
  start | stop                      Start or stop the provider network
  add NAME SOURCE [ARGS...] [-L INPUT=TARGET]
  remove NAME                       Remove provider and its wires; unbind if selected
  select NAME[.OUTPUT]|-             Choose the output exported as @inference_endpoint
  components [--json] [--components-root DIR]  List available component sources
  show [--json]                     Show configured providers and selected output
  status [--json]                   Show provider health and the active endpoint
  models [--json]                   List models exported by @inference_endpoint
  gateway [--name NAME] COMMAND      providers, models, usage, login, logout, rename
  version

add extracts -L/--link INPUT=TARGET anywhere in its arguments. The first two
remaining tokens are NAME and SOURCE; the rest are literal component arguments.

init creates configuration only. Every other mutation saves its desired change
and applies it. show and status never apply changes. Globals and external
consumers live in the selected shared dcomp system, not in a host-wide registry.
`

type CLI struct {
	Out, Err   io.Writer
	In         io.Reader
	NewRuntime func(Store, Config, io.Writer) (*Runtime, error)
}

func (cli CLI) flags(name string) *flag.FlagSet {
	f := flag.NewFlagSet(name, flag.ContinueOnError)
	f.SetOutput(cli.Err)
	return f
}

// parseOptions permits interspersed options within a command. Command dispatch
// still uses flag.Parse so options belonging to a subcommand reach its parser.
func parseOptions(flags *flag.FlagSet, args []string) error {
	var options, positional []string
	for i := 0; i < len(args); i++ {
		arg := args[i]
		if arg == "--" {
			positional = append(positional, args[i+1:]...)
			break
		}
		if arg == "-" || !strings.HasPrefix(arg, "-") {
			positional = append(positional, arg)
			continue
		}
		name := strings.TrimPrefix(strings.TrimPrefix(arg, "-"), "-")
		name, _, inline := strings.Cut(name, "=")
		option := flags.Lookup(name)
		if option == nil {
			// Keep flag's normal unknown-option and help diagnostics.
			return flags.Parse([]string{arg})
		}
		options = append(options, arg)
		boolean, ok := option.Value.(interface{ IsBoolFlag() bool })
		if !inline && !(ok && boolean.IsBoolFlag()) {
			if i+1 == len(args) {
				return flags.Parse([]string{arg})
			}
			i++
			options = append(options, args[i])
		}
	}
	options = append(options, "--")
	return flags.Parse(append(options, positional...))
}

func DefaultRoot() (string, error) {
	if value := os.Getenv("ASYS_INFERENCE_STATE_ROOT"); value != "" {
		return filepath.Abs(value)
	}
	if value := os.Getenv("ASYS_STATE_ROOT"); value != "" {
		return filepath.Abs(filepath.Join(value, "inference"))
	}
	base := os.Getenv("XDG_STATE_HOME")
	if base == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		base = filepath.Join(home, ".local/state")
	}
	return filepath.Abs(filepath.Join(base, "asys/inference"))
}

func (cli CLI) Run(ctx context.Context, args []string) int {
	if cli.NewRuntime == nil {
		cli.NewRuntime = NewRuntime
	}
	err := cli.run(ctx, args)
	if err == nil {
		return 0
	}
	if errors.Is(err, flag.ErrHelp) {
		return 0
	}
	fmt.Fprintln(cli.Err, "error:", err)
	if ctx.Err() != nil {
		return 130
	}
	var usage *usageError
	if errors.As(err, &usage) {
		return 2
	}
	return 1
}

type usageError struct{ message string }

func (e *usageError) Error() string { return e.message }
func usage(message string) error    { return &usageError{message} }

func (cli CLI) run(ctx context.Context, args []string) error {
	flags := cli.flags("asys-inference")
	root := flags.String("root", "", "machine configuration/state directory")
	flags.Usage = func() { fmt.Fprint(cli.Err, Usage) }
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() == 0 {
		fmt.Fprint(cli.Err, Usage)
		return usage("a command is required")
	}
	command := flags.Arg(0)
	args = flags.Args()[1:]
	if command == "version" {
		if len(args) != 0 {
			return usage("version takes no arguments")
		}
		fmt.Fprintf(cli.Out, "asys-inference %s (dcomp 0.3.1; %s)\n", Version, Service)
		return nil
	}
	if command == "help" {
		fmt.Fprint(cli.Out, Usage)
		return nil
	}
	switch command {
	case "components", "show", "init", "status", "models", "gateway", "start", "stop", "add", "remove", "select":
	default:
		return usage("unknown command " + command)
	}
	var err error
	if *root == "" {
		*root, err = DefaultRoot()
	} else {
		*root, err = filepath.Abs(*root)
	}
	if err != nil {
		return err
	}
	store := Store{Root: *root}
	if command == "components" {
		return cli.components(store, args)
	}
	if command == "show" {
		document, err := store.Read()
		if err != nil {
			return fmt.Errorf("read inference state (run init first): %w", err)
		}
		return cli.show(document, args)
	}
	if command == "init" {
		return cli.init(ctx, store, args)
	}
	unlock, err := store.Lock(ctx)
	if err != nil {
		return err
	}
	defer unlock()
	document, err := store.Read()
	if err != nil {
		return fmt.Errorf("read inference state (run init first): %w", err)
	}
	runtime, err := cli.NewRuntime(store, document.Config, cli.Err)
	if err != nil {
		return err
	}
	if command == "status" {
		return cli.status(ctx, runtime, document, args)
	}
	if command == "gateway" {
		return cli.gateway(ctx, runtime, document, args)
	}
	if command == "models" {
		return cli.models(ctx, runtime, document, args)
	}
	// Resume our own saved runtime intent before accepting a new desired edit.
	if err = runtime.Recover(ctx, document); err != nil {
		return err
	}
	next := Clone(document)
	if err = cli.mutate(&next.Config, command, args); err != nil {
		return err
	}
	if command != "stop" {
		if err = next.Config.Validate(); err != nil {
			return err
		}
	}
	next.Revision++
	if err = store.Write(next); err != nil {
		return err
	}
	if err = runtime.Sync(ctx, next); err != nil {
		return err
	}
	if !next.Config.Running {
		fmt.Fprintln(cli.Out, "Provider network stopped.")
	} else if next.Config.Endpoint == "" {
		fmt.Fprintf(cli.Out, "@%s is unbound.\n", GlobalName)
	} else {
		fmt.Fprintf(cli.Out, "@%s -> %s (ready)\n", GlobalName, next.Config.Endpoint)
	}
	return nil
}

func (cli CLI) init(ctx context.Context, store Store, args []string) error {
	flags := cli.flags("init")
	system := flags.String("system", "asys", "shared dcomp system")
	prefix := flags.String("prefix", "asys-inference", "physical component prefix")
	droot := flags.String("dcomp-state-root", "", "shared dcomp state root")
	rroot := flags.String("runtime-root", "", "shared dcomp proxy root")
	components := flags.String("components-root", "", "default component directory")
	empty := flags.Bool("empty", false, "start with no gateway")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return usage("init takes options only")
	}
	unlock, err := store.Lock(ctx)
	if err != nil {
		return err
	}
	defer unlock()
	if _, err = store.Read(); err == nil {
		return fmt.Errorf("machine already initialized at %s", store.Root)
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if *droot == "" {
		*droot, err = state.DefaultRoot()
		if err != nil {
			return err
		}
	}
	*droot, err = filepath.Abs(*droot)
	if err != nil {
		return err
	}
	if *rroot == "" {
		*rroot, err = proxy.DefaultRuntimeRoot(*droot)
		if err != nil {
			return err
		}
	}
	*rroot, err = filepath.Abs(*rroot)
	if err != nil {
		return err
	}
	*components, err = componentDirectory(*components)
	if err != nil {
		return err
	}
	config := Config{System: *system, DCompRoot: *droot, RuntimeRoot: *rroot, ComponentsRoot: *components, Prefix: *prefix, Running: true, Providers: []Provider{}}
	if !*empty {
		if err = cli.add(&config, []string{"gateway", "gateway"}); err != nil {
			return err
		}
	}
	if err = config.Validate(); err != nil {
		return err
	}
	document, err := NewDocument(config)
	if err != nil {
		return err
	}
	if err = store.Write(document); err != nil {
		return err
	}
	fmt.Fprintf(cli.Out, "Initialized %s; run asys-inference --root %s start\n", store.Root, store.Root)
	return nil
}

func (cli CLI) mutate(config *Config, command string, args []string) error {
	switch command {
	case "start", "stop":
		if len(args) != 0 {
			return usage(command + " takes no arguments")
		}
		config.Running = command == "start"
		return nil
	case "select":
		if len(args) != 1 {
			return usage("select requires NAME[.OUTPUT] or -")
		}
		return config.Select(args[0])
	case "remove":
		if len(args) != 1 {
			return usage("remove requires NAME")
		}
		found := false
		providers := []Provider{}
		for _, p := range config.Providers {
			if p.Name == args[0] {
				found = true
				continue
			}
			for input, target := range p.Links {
				if strings.HasPrefix(target, args[0]+".") {
					delete(p.Links, input)
				}
			}
			providers = append(providers, p)
		}
		if !found {
			return fmt.Errorf("unknown provider %s", args[0])
		}
		config.Providers = providers
		if strings.HasPrefix(config.Endpoint, args[0]+".") {
			config.Endpoint = ""
		}
		return nil
	case "add":
		return cli.add(config, args)
	}
	return usage("unknown mutation " + command)
}

func (cli CLI) add(config *Config, args []string) error {
	links := map[string]string{}
	var positional []string
	for i := 0; i < len(args); i++ {
		arg := args[i]
		var binding string
		switch {
		case arg == "-L" || arg == "--link":
			if i+1 == len(args) {
				return usage(arg + " requires INPUT=TARGET")
			}
			i++
			binding = args[i]
		case strings.HasPrefix(arg, "--link="):
			binding = strings.TrimPrefix(arg, "--link=")
		case strings.HasPrefix(arg, "-L="):
			binding = strings.TrimPrefix(arg, "-L=")
		default:
			positional = append(positional, arg)
			continue
		}
		input, target, ok := strings.Cut(binding, "=")
		if !ok || input == "" || target == "" {
			return usage("link expects INPUT=TARGET")
		}
		if _, exists := links[input]; exists {
			return usage("duplicate input binding " + input)
		}
		links[input] = target
	}
	if len(positional) < 2 {
		return usage("add requires NAME SOURCE [ARGS...] [-L INPUT=TARGET]")
	}
	provider := Provider{Name: positional[0], Source: positional[1], Links: links}
	for _, existing := range config.Providers {
		if existing.Name == provider.Name {
			return fmt.Errorf("provider already exists: %s", provider.Name)
		}
	}
	var err error
	provider.Source, provider.Context, err = CanonicalSource(provider.Source, "")
	if err != nil {
		return err
	}
	provider.Runtime, err = config.RuntimeDefaults(provider)
	if err != nil {
		return err
	}
	if len(positional) > 2 {
		provider.Runtime.Args = positional[2:]
	}
	config.Providers = append(config.Providers, provider)
	return config.Select(provider.Name)
}

func writeJSON(out io.Writer, value any) error {
	encoder := json.NewEncoder(out)
	encoder.SetIndent("", "  ")
	return encoder.Encode(value)
}
func (cli CLI) show(document *Document, args []string) error {
	flags := cli.flags("show")
	jsonOutput := flags.Bool("json", false, "JSON configuration")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return usage("show takes no arguments")
	}
	if *jsonOutput {
		return writeJSON(cli.Out, document.Config)
	}
	config := document.Config
	state := "running"
	if !config.Running {
		state = "stopped"
	}
	fmt.Fprintf(cli.Out, "Configured provider network (dcomp system %s)\nRequested state: %s\nDcomp state directory: %s\nRuntime directory: %s\n", config.System, state, config.DCompRoot, config.RuntimeRoot)
	writer := tabwriter.NewWriter(cli.Out, 0, 4, 2, ' ', 0)
	fmt.Fprintln(writer, "PROVIDER\tSOURCE\tARGUMENTS\tCONNECTIONS")
	for _, p := range config.Providers {
		keys := make([]string, 0, len(p.Links))
		for key := range p.Links {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		var links []string
		for _, key := range keys {
			links = append(links, key+"="+p.Links[key])
		}
		arguments := "-"
		if len(p.Runtime.Args) > 0 {
			data, _ := json.Marshal(p.Runtime.Args)
			arguments = string(data)
		}
		fmt.Fprintf(writer, "%s\t%s\t%s\t%s\n", p.Name, p.Source, arguments, strings.Join(links, ", "))
	}
	if err := writer.Flush(); err != nil {
		return err
	}
	endpoint := config.Endpoint
	if endpoint == "" || !config.Running {
		endpoint = "unbound"
	}
	fmt.Fprintf(cli.Out, "Selected output: @%s -> %s\n", GlobalName, endpoint)
	return nil
}
