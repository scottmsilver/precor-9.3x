#!/usr/bin/env bash
# Pure, sourceable helpers for parsing deploy/manifest.txt as DATA.
# No side effects at source time. Mirrors lib.sh load_secrets: parse,
# never execute; fail closed on anything unexpected.

# Emit validated, tab-normalized rows: "kind\tsrc\tdest\tmode\towner".
# Skips blank lines and lines whose first non-whitespace char is '#'.
# Returns non-zero (and prints to stderr) on the first invalid row.
manifest_rows() {
  local file=${1:-} line trimmed kind src dest mode owner rc=0
  [ -n "$file" ] || { echo "manifest_rows: file argument required" >&2; return 1; }
  [ -f "$file" ] || { echo "manifest not found: $file" >&2; return 1; }
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%$'\r'}
    trimmed=${line#"${line%%[![:space:]]*}"}
    [ -z "$trimmed" ] && continue
    case $trimmed in '#'*) continue ;; esac
    # Exactly five whitespace-separated fields.
    # shellcheck disable=SC2086
    set -- $trimmed
    if [ "$#" -ne 5 ]; then
      echo "manifest: row must have 5 fields: $line" >&2; return 1
    fi
    kind=$1 src=$2 dest=$3 mode=$4 owner=$5
    case $kind in bin|tree|file|unit) ;; *)
      echo "manifest: unknown kind '$kind': $line" >&2; return 1 ;; esac
    case $src in
      /*) echo "manifest: src must be repo-relative (no leading /): $line" >&2; return 1 ;;
    esac
    case "/$src/" in
      */../*) echo "manifest: src contains '..': $line" >&2; return 1 ;;
    esac
    case $dest in
      /usr/local/bin/*|/etc/systemd/system/*|/etc/avahi/services/*|'~/'*|/home/*) ;;
      *) echo "manifest: dest outside allowed roots: $line" >&2; return 1 ;;
    esac
    case $mode in [0-7][0-7][0-7][0-7]) ;; *)
      echo "manifest: mode must be 4 octal digits: $line" >&2; return 1 ;; esac
    case $owner in root|@USER@) ;; *)
      echo "manifest: owner must be root or @USER@: $line" >&2; return 1 ;; esac
    printf '%s\t%s\t%s\t%s\t%s\n' "$kind" "$src" "$dest" "$mode" "$owner"
  done < "$file"
  return $rc
}

# Resolve @USER@ / leading ~ in a dest path for a concrete user.
manifest_resolve_dest() {
  local dest=${1:-} user=${2:-}
  [ -n "$dest" ] && [ -n "$user" ] || { echo "manifest_resolve_dest: dest and user required" >&2; return 1; }
  dest=${dest//@USER@/$user}
  case $dest in '~/'*) dest="/home/$user/${dest#\~/}" ;; esac
  printf '%s\n' "$dest"
}
