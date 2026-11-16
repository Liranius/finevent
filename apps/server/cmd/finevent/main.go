package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"finevent/internal/calendar"
	"finevent/internal/probe"
	"golang.org/x/term"
	_ "time/tzdata"
)

func main() {
	initConfig := flag.Bool("init", false, "initialize secrets without overwriting existing config")
	configureAI := flag.Bool("configure-ai", false, "configure the server-only DeepSeek key via hidden terminal input")
	dev := flag.Bool("dev", false, "allow loopback-only development initialization")
	dir := flag.String("data", "var", "data directory")
	listen := flag.String("listen", "127.0.0.1:8787", "listen address")
	origin := flag.String("origin", "", "site origin (required with -init)")
	contact := flag.String("contact", "", "VAPID contact URI (defaults to origin + /finevent/)")
	trend := flag.String("trendradar", "", "read-only TrendRadar output directory")
	collect := flag.Bool("collect", false, "enable source sampling")
	calendarPackage := flag.String("import-calendar-package", "", "import a reviewed calendar ZIP into the data directory and exit")
	flag.Parse()
	if *dev && *origin == "" {
		*origin = "http://" + *listen
	}
	if *initConfig && *origin == "" {
		log.Fatal("-origin is required when initializing a production configuration")
	}
	if *contact == "" {
		*contact = strings.TrimRight(*origin, "/") + "/finevent/"
	}
	if *configureAI {
		fmt.Print("DeepSeek API Key（不回显，仅写入服务端私有配置）：")
		b, e := term.ReadPassword(int(os.Stdin.Fd()))
		fmt.Println()
		if e != nil {
			log.Fatal("无法读取终端输入")
		}
		if e = probe.ConfigureAI(*dir, string(b)); e != nil {
			log.Fatal(e)
		}
		fmt.Println("AI 配置已保存；重启服务后在页面启用解读。")
		return
	}
	if *initConfig {
		fmt.Print("设置 finevent 登录密码（12–72 字节，不回显）：")
		b, err := term.ReadPassword(int(os.Stdin.Fd()))
		fmt.Println()
		if err != nil {
			log.Fatal(err)
		}
		_, err = probe.InitConfig(*dir, *origin, string(b), *contact)
		if err != nil {
			log.Fatal(err)
		}
		fmt.Println("配置已创建。密码及推送私钥不写入日志。")
		return
	}
	if *dev {
		host, _, err := net.SplitHostPort(*listen)
		if err != nil || (host != "127.0.0.1" && host != "localhost") {
			log.Fatal("development server must bind loopback")
		}
		if _, err = os.Stat(filepath.Join(*dir, "config.json")); os.IsNotExist(err) {
			_, err = probe.InitConfig(*dir, *origin, os.Getenv("FINEVENT_TEST_PASSWORD"), *contact)
			if err != nil {
				log.Fatal(err)
			}
		}
	}
	cfg, err := probe.LoadConfig(*dir)
	if err != nil {
		log.Fatal("先运行 -init 创建配置：", err)
	}
	store, err := probe.OpenStore(*dir)
	if err != nil {
		log.Fatal(err)
	}
	defer store.DB.Close()
	if *calendarPackage != "" {
		if err := (&calendar.Store{DB: store.DB}).ImportCalendarArchive(*calendarPackage); err != nil {
			log.Fatal("日历数据包导入失败：", err)
		}
		fmt.Println("日历数据包已核对并导入。")
		return
	}
	app := probe.NewApp(cfg, store)
	app.AIConfig, err = probe.LoadAIConfig(*dir)
	if err != nil {
		log.Print(err)
	}
	app.CollectionEnabled = *collect
	app.TrendEnabled = *trend != ""
	app.TrendSettingsSocket = os.Getenv("FINEVENT_TREND_SETTINGS_SOCKET")
	app.ProbeRequestPath = filepath.Join(*dir, "ai-probe-request.json")
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	backgroundDone := make(chan struct{})
	go func() { defer close(backgroundDone); app.Run(ctx, *collect, *trend) }()
	srv := &http.Server{Addr: *listen, Handler: app.Handler(), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second, WriteTimeout: 30 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 16384}
	shutdownDone := make(chan struct{})
	go func() {
		defer close(shutdownDone)
		<-ctx.Done()
		c, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = srv.Shutdown(c)
	}()
	log.Printf("finevent verification service listening on %s", *listen)
	if err = srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
	<-shutdownDone
	<-backgroundDone
}
