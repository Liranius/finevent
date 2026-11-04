<script setup lang="ts">
import { aiState, directionName, yuan, type AIJob } from '../ai';
import { newsTime, sourceName, safeNewsURL } from '../news';
defineProps<{ job: AIJob }>();
</script>

<template>
  <div class="ai-result">
    <p class="caption">
      {{ aiState[job.status] ?? job.status }} · {{ job.model }} ·
      {{ newsTime(job.completed || job.created) }}
    </p>
    <p v-if="job.stale" class="message" role="status">
      来源已修订。这份解读基于旧版本，请核对下方引用快照，或请求最新解读。
    </p>
    <p v-if="job.error" class="message error" role="status">{{ job.error }}</p>
    <template v-if="job.status === 'succeeded' && job.result">
      <h4>事实摘要 · 模型整理</h4>
      <ol class="ai-facts">
        <li v-for="(fact, index) in job.result.facts" :key="index">
          <p>{{ fact.text }}</p>
          <blockquote>
            “{{ fact.quote }}” <span class="caption">[{{ fact.evidence }}]</span>
          </blockquote>
        </li>
      </ol>
      <h4>潜在影响 · 模型推断</h4>
      <article v-for="(impact, index) in job.result.impacts" :key="index" class="ai-impact">
        <div class="ai-impact-heading">
          <h5>{{ impact.target }}</h5>
          <strong>{{ directionName[impact.direction] }}</strong>
        </div>
        <p class="caption">{{ impact.market }} · {{ impact.industry }} · {{ impact.horizon }}</p>
        <p>{{ impact.path }}</p>
        <dl>
          <dt>成立条件</dt>
          <dd>{{ impact.conditions.join('；') }}</dd>
          <dt>反向因素</dt>
          <dd>{{ impact.counterfactors.join('；') }}</dd>
        </dl>
        <p class="caption">依据 [{{ impact.evidence.join('、') }}]</p>
      </article>
      <section v-if="job.result.metrics.length" aria-label="有来源的指标">
        <h4>指标与口径</h4>
        <article v-for="metric in job.result.metrics" :key="metric.name" class="ai-metric">
          <h5>{{ metric.name }}</h5>
          <dl>
            <template v-for="field in ['actual', 'expected', 'previous'] as const" :key="field">
              <dt>{{ { actual: '实际值', expected: '预期值', previous: '前值' }[field] }}</dt>
              <dd>
                {{ metric[field]?.value ?? '来源未提供'
                }}<span v-if="metric[field]" class="caption">
                  · [{{ metric[field].evidence }}] “{{ metric[field].quote }}”</span
                >
              </dd>
            </template>
          </dl>
        </article>
      </section>
      <p class="message">不确定因素：{{ job.result.uncertainty }}</p>
      <p class="caption">
        引用已核对是否存在，推断仍需结合条件判断；未接入实际行情，不构成走势预测。
      </p>
    </template>
    <details v-if="job.calls.length" class="ai-call-records">
      <summary>调用与费用记录</summary>
      <ul class="plain-list">
        <li v-for="(call, index) in job.calls" :key="index">
          <p>{{ newsTime(call.started) }} · {{ aiState[call.status] ?? call.status }}</p>
          <p>
            {{
              call.estimated_nano === null
                ? `费用未结，保留预留 ${yuan(call.reserved_nano)}`
                : `用量估算 ${yuan(call.estimated_nano)}`
            }}
          </p>
          <p class="caption">
            输入 {{ call.prompt_tokens ?? '未知' }} / 输出
            {{ call.completion_tokens ?? '未知' }} token · 价格 {{ call.price_version }}<br />{{
              call.price_basis.startsWith('off_peak')
                ? '按请求所在空闲时段估算'
                : '按配置高峰价保守计入'
            }}{{ call.price_basis.endsWith('cache_unknown') ? '；缓存用量未知，按未命中估算' : '' }}
          </p>
        </li>
      </ul>
    </details>
    <details class="ai-snapshots">
      <summary>引用快照 · {{ job.evidence.length }} 份</summary>
      <article v-for="item in job.evidence" :key="item.id">
        <h5>[{{ item.id }}] {{ item.title }}</h5>
        <p class="caption">
          {{ sourceName(item.source) }} · 第 {{ item.version }} 版 · 来源发布
          {{ newsTime(item.published_at) }}
        </p>
        <a
          v-if="safeNewsURL(item.url)"
          :href="safeNewsURL(item.url)"
          target="_blank"
          rel="noopener noreferrer"
          >查看引用原文 ↗</a
        >
        <p class="news-copy">{{ item.text || '来源只提供标题' }}</p>
        <p v-if="item.truncated" class="news-warning">输入已按长度截取，解读仅覆盖这段内容。</p>
      </article>
      <p class="caption">
        提示词 {{ job.prompt_version }} · 快照保留至
        {{ newsTime(job.expires) }}。缓存复用保留首次解读使用的来源版本。
      </p>
    </details>
  </div>
</template>
