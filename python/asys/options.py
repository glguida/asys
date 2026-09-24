"""Shared meanings and help for host state and dcomp connection options."""
from pathlib import Path

ROOT_HELP = ('asys state directory containing runs, inference and human state '
             '(default: ASYS_STATE_ROOT, then XDG_STATE_HOME/asys, then ~/.local/state/asys)')


def dcomp_options(parser):
    parser.add_argument('--system', default='asys', metavar='NAME',
        help='dcomp namespace containing components and shared endpoints (default: asys)')
    parser.add_argument('--dcomp-state-root', type=Path, metavar='DIRECTORY',
        help='dcomp state directory (default: DCOMP_STATE_ROOT or dcomp default)')
    parser.add_argument('--runtime-root', type=Path, metavar='DIRECTORY',
        help='dcomp proxy/socket directory (default: DCOMP_RUNTIME_ROOT or the dcomp state directory/run)')
