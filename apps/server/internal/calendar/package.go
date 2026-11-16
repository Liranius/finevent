package calendar

import (
	"archive/zip"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path"
	"time"
)

const maxSnapshotBytes = 2 << 20
const maxCalendarPackageBytes = 16 << 20
const maxPackageEntries = 32

type packageEntry struct {
	SnapshotInfo
	Source string `json:"source"`
	File   string `json:"file"`
}

type packageManifest struct {
	Version   int            `json:"version"`
	Snapshots []packageEntry `json:"snapshots"`
}

func (s *Store) ImportCalendarArchive(filename string) error {
	f, err := os.Open(filename)
	if err != nil {
		return err
	}
	defer f.Close()
	stat, err := f.Stat()
	if err != nil {
		return err
	}
	if !stat.Mode().IsRegular() || stat.Size() > maxCalendarPackageBytes {
		return errors.New("日历数据包必须是 16 MiB 以内的 ZIP 文件")
	}
	z, err := zip.NewReader(f, stat.Size())
	if err != nil {
		return err
	}
	if len(z.File) > maxPackageEntries+1 {
		return errors.New("日历数据包文件过多")
	}
	return s.ImportCalendarPackage(z, time.Now().Unix())
}

func readPackageFile(files fs.FS, name string, limit int64) ([]byte, error) {
	if !fs.ValidPath(name) || path.Base(name) != name || name == "." {
		return nil, errors.New("无效的数据包文件名")
	}
	f, err := files.Open(name)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	b, err := io.ReadAll(io.LimitReader(f, limit+1))
	if int64(len(b)) > limit {
		return nil, errors.New("日历数据包文件过大")
	}
	return b, err
}

// ImportCalendarPackage applies all reviewed sources in manifest order in one transaction.
func (s *Store) ImportCalendarPackage(files fs.FS, now int64) error {
	b, err := readPackageFile(files, "manifest.json", 64<<10)
	if err != nil {
		return err
	}
	var m packageManifest
	if err := json.Unmarshal(b, &m); err != nil {
		return err
	}
	if m.Version != 1 || len(m.Snapshots) == 0 || len(m.Snapshots) > maxPackageEntries {
		return errors.New("不支持的日历数据包版本或清单条数")
	}
	tx, err := s.DB.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	seen := map[string]bool{"manifest.json": true}
	total := 0
	for _, entry := range m.Snapshots {
		if seen[entry.File] {
			return fmt.Errorf("重复的文件：%s", entry.File)
		}
		seen[entry.File] = true
		body, err := readPackageFile(files, entry.File, maxSnapshotBytes)
		if err != nil {
			return err
		}
		total += len(body)
		if total > maxCalendarPackageBytes {
			return errors.New("日历数据包解压后过大")
		}
		if err := importPackageEntry(tx, entry, body, now); err != nil {
			return fmt.Errorf("%s: %w", entry.File, err)
		}
	}
	return tx.Commit()
}

func (i SnapshotInfo) reviewFields() SnapshotInfo {
	i.ImportedAt, i.ImportedEntries, i.ActiveEntries, i.CoverageEnd = 0, 0, 0, ""
	return i
}

func importPackageEntry(tx *sql.Tx, entry packageEntry, body []byte, now int64) error {
	info := entry.SnapshotInfo
	if info != info.reviewFields() {
		return errors.New("清单含有运行时状态")
	}
	var err error
	switch entry.Source {
	case "nbs":
		err = importNBSSnapshot(tx, info, body, now)
	case "bls":
		err = importBLSTable(tx, info, body, now)
	default:
		return errors.New("不支持的日历来源")
	}
	if err != nil {
		return err
	}

	// On replay, confirm the review describes the existing evidence before recording a receipt.
	var raw []byte
	var saved SnapshotInfo
	if err := tx.QueryRow(`SELECT metadata FROM calendar_snapshots WHERE id=? AND source=?`, info.ID, entry.Source).Scan(&raw); err != nil {
		return err
	}
	if err := json.Unmarshal(raw, &saved); err != nil {
		return err
	}
	if saved.reviewFields() != info {
		return errors.New("相同快照的核对记录不一致")
	}
	raw, err = json.Marshal(info)
	if err != nil {
		return err
	}
	_, err = tx.Exec(`INSERT INTO calendar_package_reviews(id,metadata) VALUES(?,?) ON CONFLICT(id) DO NOTHING`, info.ID, string(raw))
	return err
}
