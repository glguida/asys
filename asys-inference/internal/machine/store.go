package machine

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"syscall"
	"time"

	"github.com/glguida/dcomp/hostfs"
)

const documentLimit = 16 * 1024 * 1024

type Store struct{ Root string }

func (store Store) Lock(ctx context.Context) (func(), error) {
	if !filepath.IsAbs(store.Root) {
		return nil, fmt.Errorf("machine root must be absolute")
	}
	if err := hostfs.MkdirAll(store.Root, 0700); err != nil {
		return nil, err
	}
	file, err := hostfs.OpenFile(filepath.Join(store.Root, "lock"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	for {
		if err = ctx.Err(); err != nil {
			file.Close()
			return nil, err
		}
		err = syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			return func() { file.Close() }, nil
		}
		if !errors.Is(err, syscall.EWOULDBLOCK) && !errors.Is(err, syscall.EAGAIN) {
			file.Close()
			return nil, err
		}
		timer := time.NewTimer(25 * time.Millisecond)
		select {
		case <-ctx.Done():
			timer.Stop()
		case <-timer.C:
		}
	}
}

func (store Store) Read() (*Document, error) {
	file, err := os.Open(filepath.Join(store.Root, "machine.json"))
	if err != nil {
		return nil, err
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, documentLimit+1))
	if err != nil {
		return nil, err
	}
	if len(data) > documentLimit {
		return nil, fmt.Errorf("machine configuration exceeds 16 MiB")
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	var document Document
	if err = decoder.Decode(&document); err != nil {
		return nil, fmt.Errorf("read machine configuration: %w", err)
	}
	var extra any
	if err = decoder.Decode(&extra); err != io.EOF {
		return nil, fmt.Errorf("configuration contains trailing data")
	}
	if document.Version != 1 || len(document.ID) != 32 || document.Revision == 0 {
		return nil, fmt.Errorf("unsupported or invalid inference machine state")
	}
	return &document, nil
}

func (store Store) Write(document *Document) error {
	data, err := json.MarshalIndent(document, "", "  ")
	if err != nil {
		return err
	}
	if len(data)+1 > documentLimit {
		return fmt.Errorf("machine configuration exceeds 16 MiB")
	}
	return AtomicWrite(filepath.Join(store.Root, "machine.json"), append(data, '\n'))
}

func AtomicWrite(path string, data []byte) error {
	file, err := os.CreateTemp(filepath.Dir(path), ".asys-inference-*")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	if err := file.Chmod(hostfs.Mode(path, 0600)); err != nil {
		file.Close()
		return err
	}
	if _, err = io.Copy(file, bytes.NewReader(data)); err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return err
	}
	if err = os.Rename(file.Name(), path); err != nil {
		return err
	}
	dir, err := os.Open(filepath.Dir(path))
	if err != nil {
		return err
	}
	defer dir.Close()
	return dir.Sync()
}
