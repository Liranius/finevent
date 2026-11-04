package news

import (
	"slices"
	"testing"
)

func TestLabelsExplainTopicsWithoutInventingImpact(t *testing.T) {
	r := Classify("央行宣布降准，银行板块受关注", "未提供任何实际数值")
	if !slices.Contains(r.Categories, "monetary") || !slices.Contains(r.Industries, "finance") || r.Importance != 3 || len(r.Reasons) == 0 || r.Version != Version {
		t.Fatalf("%+v", r)
	}
	if r := Classify("chairman discusses daily activity", ""); len(r.Industries) != 0 || len(r.Categories) != 0 {
		t.Fatalf("ASCII substring false positive: %+v", r)
	}
	if r := Classify("AI芯片与半导体产业", ""); !slices.Contains(r.Industries, "technology") {
		t.Fatal(r)
	}
	if len(Categories()) != 10 {
		t.Fatal("taxonomy incomplete")
	}
	if r := Classify("央行开展逆回购操作", ""); slices.Contains(r.Categories, "company") || !slices.Contains(r.Categories, "monetary") {
		t.Fatalf("monetary operation classified as company buyback: %+v", r)
	}
}
func TestRelationsAreConservative(t *testing.T) {
	if Fingerprint("简短通用快讯") != "" {
		t.Fatal("short copy must not link")
	}
	if Similar("央行宣布下调存款准备金率0.5个百分点", "央行宣布下调存款准备金率0.25个百分点") {
		t.Fatal("different figures linked")
	}
	if Similar("银行宣布上调存款利率，明日起正式执行", "银行宣布下调存款利率，明日起正式执行") {
		t.Fatal("opposite actions linked")
	}
	if !Similar("央行宣布下调存款准备金率0.5个百分点", "央行：宣布下调存款准备金率0.5个百分点") {
		t.Fatal("punctuation variant not related")
	}
}
