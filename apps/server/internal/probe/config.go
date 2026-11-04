package probe

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	webpush "github.com/SherClockHolmes/webpush-go"
	"golang.org/x/crypto/bcrypt"
)

type Config struct {
	Origin       string `json:"origin"`
	PasswordHash string `json:"password_hash"`
	VAPIDPublic  string `json:"vapid_public"`
	VAPIDPrivate string `json:"vapid_private"`
	Contact      string `json:"contact"`
}

func InitConfig(dir, origin, password, contact string) (Config, error) {
	var c Config
	u, err := url.Parse(origin)
	if err != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return c, errors.New("origin 必须为不含路径的完整站点地址")
	}
	if u.Scheme != "https" && !(u.Scheme == "http" && (u.Hostname() == "127.0.0.1" || u.Hostname() == "localhost")) {
		return c, errors.New("生产站点必须使用 HTTPS")
	}
	if len(password) < 12 || len(password) > 72 {
		return c, errors.New("密码须为 12–72 字节")
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(password), 12)
	if err != nil {
		return c, err
	}
	private, public, err := webpush.GenerateVAPIDKeys()
	if err != nil {
		return c, err
	}
	c = Config{strings.TrimSuffix(origin, "/"), string(hash), public, private, contact}
	if err := os.MkdirAll(dir, 0700); err != nil {
		return c, err
	}
	f, err := os.OpenFile(filepath.Join(dir, "config.json"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return c, err
	}
	defer f.Close()
	err = json.NewEncoder(f).Encode(c)
	return c, err
}

func LoadConfig(dir string) (Config, error) {
	var c Config
	b, err := os.ReadFile(filepath.Join(dir, "config.json"))
	if err != nil {
		return c, err
	}
	err = json.Unmarshal(b, &c)
	if err == nil && (c.Origin == "" || c.PasswordHash == "" || c.VAPIDPublic == "" || c.VAPIDPrivate == "") {
		err = errors.New("配置不完整")
	}
	return c, err
}

func token() string { return hex.EncodeToString(randomBytes(32)) }
func randomBytes(n int) []byte {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return b
}
func digest(s string) string { h := sha256.Sum256([]byte(s)); return hex.EncodeToString(h[:]) }
