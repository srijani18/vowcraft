import type { CredentialModule } from './types'

/**
 * Service catalogue — SPEC-004 §6.
 *
 * Data, not scattered conditionals: each entry declares its module, fields, env
 * fallback, docs link, pricing tier, and how to verify. Adding a provider is one
 * entry here and nothing else.
 *
 * Ordering within a module is deliberate: **free tiers first**, so the default
 * path through the settings page costs nothing. `tier` drives the badge and the
 * sort, and `costNote` states the actual limit rather than a vague "free" claim
 * that will be wrong in six months.
 */

export type PricingTier = 'free' | 'freemium' | 'paid' | 'local'

export interface CredentialField {
  key: string
  label: string
  placeholder: string
  /** Rendered as a password input and masked in every response. */
  secret: boolean
  required: boolean
  help?: string
}

export interface VerifyRecipe {
  /** Cheapest authenticated read the provider offers. */
  url: string
  method?: 'GET' | 'POST'
  /** How the key is presented. `header` names the header for the 'header' mode. */
  auth: 'bearer' | 'header' | 'query'
  header?: string
  extraHeaders?: Record<string, string>
}

export interface ServiceSpec {
  service: string
  displayName: string
  module: CredentialModule
  blurb: string
  tier: PricingTier
  /** The concrete allowance, e.g. "14,400 requests/day". */
  costNote: string
  fields: CredentialField[]
  /** Env var consulted for the ENV tier of the resolution order (SPEC-004 §3). */
  envVar?: string
  docsUrl: string
  verify?: VerifyRecipe
  /** True when the service needs no key at all (a local model). */
  localOnly?: boolean
  /**
   * Another service whose key this one also uses.
   *
   * Several providers cover more than one module from a single account: one Groq key
   * serves both `/audio/transcriptions` and `/chat/completions`, and one OpenAI key
   * serves audio, chat, and embeddings. Listing them as separate catalogue entries is
   * right for the UI — they are genuinely different capabilities with different costs —
   * but a user who pastes their key once must not then find the *other* module
   * reporting "no provider configured". This links them.
   */
  sharesKeyWith?: string
  /** Representative model ids, shown as a hint in the UI. */
  models?: string[]
}

const API_KEY = (placeholder: string, help?: string): CredentialField => ({
  key: 'apiKey',
  label: 'API key',
  placeholder,
  secret: true,
  required: true,
  help,
})

/** Most providers speak the OpenAI wire format, so one recipe covers many. */
const openaiCompatibleVerify = (base: string): VerifyRecipe => ({
  url: `${base}/models`,
  auth: 'bearer',
})

export const CATALOG: readonly ServiceSpec[] = [
  // ══════════════════════════════════════════════════════ TRANSCRIPTION ══
  {
    service: 'groq',
    displayName: 'Groq — Whisper large-v3 turbo',
    module: 'TRANSCRIPTION',
    blurb: 'Fastest free Whisper available. The recommended default.',
    tier: 'free',
    costNote: 'Free tier: ~28,800 audio-seconds/day, no card required.',
    fields: [API_KEY('gsk_…')],
    envVar: 'GROQ_API_KEY',
    docsUrl: 'https://console.groq.com/keys',
    verify: openaiCompatibleVerify('https://api.groq.com/openai/v1'),
    models: ['whisper-large-v3-turbo', 'whisper-large-v3'],
  },
  {
    service: 'local_whisper',
    displayName: 'Local Whisper / WhisperX',
    module: 'TRANSCRIPTION',
    blurb: 'Runs in the asr container with diarization. No data leaves the machine.',
    tier: 'local',
    costNote: 'Free forever. Needs ~2 GB RAM for the small model.',
    fields: [],
    docsUrl: 'https://github.com/m-bain/whisperX',
    localOnly: true,
    models: ['whisper-small', 'whisper-large-v3 + pyannote'],
  },
  {
    service: 'assemblyai',
    displayName: 'AssemblyAI',
    module: 'TRANSCRIPTION',
    blurb: 'Diarization, word timings, and summaries in a single call.',
    tier: 'freemium',
    costNote: '$50 free credit, then ~$0.12/hour.',
    fields: [API_KEY('…')],
    envVar: 'ASSEMBLYAI_API_KEY',
    docsUrl: 'https://www.assemblyai.com/app/account',
    verify: { url: 'https://api.assemblyai.com/v2/transcript?limit=1', auth: 'header', header: 'authorization' },
  },
  {
    service: 'deepgram',
    displayName: 'Deepgram Nova',
    module: 'TRANSCRIPTION',
    blurb: 'Best streaming latency; built-in diarization. Good for live meetings.',
    tier: 'freemium',
    costNote: '$200 free credit, then ~$0.26/hour.',
    fields: [API_KEY('…')],
    envVar: 'DEEPGRAM_API_KEY',
    docsUrl: 'https://console.deepgram.com',
    verify: { url: 'https://api.deepgram.com/v1/projects', auth: 'header', header: 'Authorization' },
    models: ['nova-3', 'nova-2'],
  },
  {
    service: 'elevenlabs',
    displayName: 'ElevenLabs Scribe',
    module: 'TRANSCRIPTION',
    blurb: 'Strong accuracy on accented and noisy audio, 99 languages.',
    tier: 'freemium',
    costNote: 'Free tier available; paid from ~$0.22/hour.',
    fields: [API_KEY('sk_…')],
    envVar: 'ELEVENLABS_API_KEY',
    docsUrl: 'https://elevenlabs.io/app/settings/api-keys',
    verify: { url: 'https://api.elevenlabs.io/v1/user', auth: 'header', header: 'xi-api-key' },
  },
  {
    service: 'openai_audio',
    displayName: 'OpenAI — Whisper / gpt-4o-transcribe',
    module: 'TRANSCRIPTION',
    sharesKeyWith: 'openai',
    blurb: 'Reliable baseline quality across 50+ languages.',
    tier: 'paid',
    costNote: '~$0.36/hour (whisper-1). No free tier.',
    fields: [API_KEY('sk-…')],
    envVar: 'OPENAI_API_KEY',
    docsUrl: 'https://platform.openai.com/api-keys',
    verify: openaiCompatibleVerify('https://api.openai.com/v1'),
    models: ['whisper-1', 'gpt-4o-transcribe', 'gpt-4o-mini-transcribe'],
  },

  // ═════════════════════════════════════════════════════════ EXTRACTION ══
  {
    service: 'google_gemini',
    displayName: 'Google Gemini',
    module: 'EXTRACTION',
    blurb: 'Generous free tier with reliable function calling. Strong default.',
    tier: 'free',
    costNote: 'Free tier: 1,500 requests/day on Flash models.',
    fields: [API_KEY('AIza…')],
    envVar: 'GOOGLE_GEMINI_API_KEY',
    docsUrl: 'https://aistudio.google.com/apikey',
    verify: { url: 'https://generativelanguage.googleapis.com/v1beta/models', auth: 'query' },
    models: ['gemini-2.0-flash', 'gemini-2.5-pro'],
  },
  {
    service: 'groq_llm',
    displayName: 'Groq — Llama / Qwen',
    module: 'EXTRACTION',
    // One Groq account covers both endpoints, so the key entered for transcription is
    // reused here rather than asked for twice.
    sharesKeyWith: 'groq',
    blurb: 'Sub-second extraction on open models. Uses the same key as Groq transcription.',
    tier: 'free',
    costNote: 'Free tier: 14,400 requests/day.',
    fields: [API_KEY('gsk_…')],
    envVar: 'GROQ_API_KEY',
    docsUrl: 'https://console.groq.com/keys',
    verify: openaiCompatibleVerify('https://api.groq.com/openai/v1'),
    models: ['openai/gpt-oss-120b', 'llama-3.3-70b-versatile'],
  },
  {
    service: 'cerebras',
    displayName: 'Cerebras',
    module: 'EXTRACTION',
    blurb: 'The fastest open-model inference; free developer tier.',
    tier: 'free',
    costNote: 'Free tier: 1M tokens/day.',
    fields: [API_KEY('csk-…')],
    envVar: 'CEREBRAS_API_KEY',
    docsUrl: 'https://cloud.cerebras.ai',
    verify: openaiCompatibleVerify('https://api.cerebras.ai/v1'),
    models: ['llama-3.3-70b', 'qwen-3-32b'],
  },
  {
    service: 'openrouter',
    displayName: 'OpenRouter',
    module: 'EXTRACTION',
    blurb: 'One key, 300+ models, including several genuinely free ones.',
    tier: 'freemium',
    costNote: 'Free models available (`:free` suffix); paid models at cost.',
    fields: [API_KEY('sk-or-…')],
    envVar: 'OPENROUTER_API_KEY',
    docsUrl: 'https://openrouter.ai/keys',
    verify: { url: 'https://openrouter.ai/api/v1/key', auth: 'bearer' },
    models: ['meta-llama/llama-3.3-70b-instruct:free', 'deepseek/deepseek-r1:free'],
  },
  {
    service: 'mistral',
    displayName: 'Mistral AI',
    module: 'EXTRACTION',
    blurb: 'European hosting, solid structured output, free experiment tier.',
    tier: 'freemium',
    costNote: 'Free experimentation tier; paid from ~$0.2/M tokens.',
    fields: [API_KEY('…')],
    envVar: 'MISTRAL_API_KEY',
    docsUrl: 'https://console.mistral.ai/api-keys',
    verify: openaiCompatibleVerify('https://api.mistral.ai/v1'),
    models: ['mistral-large-latest', 'mistral-small-latest'],
  },
  {
    service: 'together',
    displayName: 'Together AI',
    module: 'EXTRACTION',
    blurb: 'Broad open-model catalogue with a free starter credit.',
    tier: 'freemium',
    costNote: '$1 free credit; some models free at low rate limits.',
    fields: [API_KEY('…')],
    envVar: 'TOGETHER_API_KEY',
    docsUrl: 'https://api.together.xyz/settings/api-keys',
    verify: openaiCompatibleVerify('https://api.together.xyz/v1'),
  },
  {
    service: 'cohere',
    displayName: 'Cohere',
    module: 'EXTRACTION',
    blurb: 'Command models with a free trial key for non-production use.',
    tier: 'freemium',
    costNote: 'Free trial key: 1,000 calls/month, rate limited.',
    fields: [API_KEY('…')],
    envVar: 'COHERE_API_KEY',
    docsUrl: 'https://dashboard.cohere.com/api-keys',
    verify: { url: 'https://api.cohere.com/v1/models', auth: 'bearer' },
  },
  {
    service: 'huggingface',
    displayName: 'Hugging Face Inference',
    module: 'EXTRACTION',
    blurb: 'Serverless inference over thousands of open models.',
    tier: 'freemium',
    costNote: 'Free monthly credits on the serverless API.',
    fields: [API_KEY('hf_…')],
    envVar: 'HUGGINGFACE_API_KEY',
    docsUrl: 'https://huggingface.co/settings/tokens',
    verify: { url: 'https://huggingface.co/api/whoami-v2', auth: 'bearer' },
  },
  {
    service: 'ollama',
    displayName: 'Ollama (local)',
    module: 'EXTRACTION',
    blurb: 'Runs models on your own hardware. Nothing leaves the machine.',
    tier: 'local',
    costNote: 'Free. Set OLLAMA_BASE_URL if it is not on localhost:11434.',
    fields: [],
    docsUrl: 'https://ollama.com',
    localOnly: true,
    models: ['llama3.3', 'qwen2.5', 'mistral-nemo'],
  },
  {
    service: 'anthropic',
    displayName: 'Anthropic — Claude',
    module: 'EXTRACTION',
    blurb: 'Strongest at structured extraction and following guardrail instructions.',
    tier: 'paid',
    costNote: 'Pay per token; no free tier. Cheapest via Haiku.',
    fields: [API_KEY('sk-ant-…')],
    envVar: 'ANTHROPIC_API_KEY',
    docsUrl: 'https://console.anthropic.com/settings/keys',
    verify: {
      url: 'https://api.anthropic.com/v1/models',
      auth: 'header',
      header: 'x-api-key',
      extraHeaders: { 'anthropic-version': '2023-06-01' },
    },
    models: ['claude-sonnet-4-5', 'claude-haiku-4-5'],
  },
  {
    service: 'openai',
    displayName: 'OpenAI — GPT',
    module: 'EXTRACTION',
    blurb: 'Mature function calling and strict JSON schema support.',
    tier: 'paid',
    costNote: 'Pay per token; no free tier.',
    fields: [API_KEY('sk-…')],
    envVar: 'OPENAI_API_KEY',
    docsUrl: 'https://platform.openai.com/api-keys',
    verify: openaiCompatibleVerify('https://api.openai.com/v1'),
    models: ['gpt-4.1', 'gpt-4.1-mini'],
  },
  {
    service: 'deepseek',
    displayName: 'DeepSeek',
    module: 'EXTRACTION',
    blurb: 'Very low cost per token for high-volume batch extraction.',
    tier: 'paid',
    costNote: 'Paid, but roughly an order of magnitude cheaper than peers.',
    fields: [API_KEY('sk-…')],
    envVar: 'DEEPSEEK_API_KEY',
    docsUrl: 'https://platform.deepseek.com/api_keys',
    verify: openaiCompatibleVerify('https://api.deepseek.com/v1'),
  },
  {
    service: 'xai',
    displayName: 'xAI — Grok',
    module: 'EXTRACTION',
    blurb: 'OpenAI-compatible API with a large context window.',
    tier: 'paid',
    costNote: 'Pay per token; promotional credits appear periodically.',
    fields: [API_KEY('xai-…')],
    envVar: 'XAI_API_KEY',
    docsUrl: 'https://console.x.ai',
    verify: openaiCompatibleVerify('https://api.x.ai/v1'),
  },
  {
    service: 'azure_openai',
    displayName: 'Azure OpenAI',
    module: 'EXTRACTION',
    blurb: 'For organisations that require an Azure-resident deployment.',
    tier: 'paid',
    costNote: 'Azure billing. Needs the endpoint as well as the key.',
    fields: [
      API_KEY('…'),
      {
        key: 'endpoint',
        label: 'Endpoint',
        placeholder: 'https://my-resource.openai.azure.com',
        secret: false,
        required: true,
      },
      {
        key: 'deployment',
        label: 'Deployment name',
        placeholder: 'gpt-4.1',
        secret: false,
        required: true,
      },
    ],
    envVar: 'AZURE_OPENAI_API_KEY',
    docsUrl: 'https://learn.microsoft.com/azure/ai-services/openai/',
  },

  // ════════════════════════════════════════════════════════ TRANSLATION ══
  {
    service: 'deepl',
    displayName: 'DeepL',
    module: 'TRANSLATION',
    blurb: 'Highest translation quality for European languages.',
    tier: 'free',
    costNote: 'Free tier: 500,000 characters/month.',
    fields: [API_KEY('…:fx')],
    envVar: 'DEEPL_API_KEY',
    docsUrl: 'https://www.deepl.com/pro-api',
    verify: { url: 'https://api-free.deepl.com/v2/usage', auth: 'header', header: 'Authorization' },
  },
  {
    service: 'libretranslate',
    displayName: 'LibreTranslate (self-hosted)',
    module: 'TRANSLATION',
    blurb: 'Open-source translation you can run beside the app.',
    tier: 'local',
    costNote: 'Free. Set LIBRETRANSLATE_URL to your instance.',
    fields: [],
    docsUrl: 'https://libretranslate.com',
    localOnly: true,
  },
  {
    service: 'google_translate',
    displayName: 'Google Cloud Translation',
    module: 'TRANSLATION',
    blurb: 'Widest language coverage, including low-resource languages.',
    tier: 'freemium',
    costNote: 'First 500k characters/month free, then ~$20/M.',
    fields: [API_KEY('AIza…')],
    envVar: 'GOOGLE_TRANSLATE_API_KEY',
    docsUrl: 'https://cloud.google.com/translate/docs/setup',
  },

  // ═════════════════════════════════════════════ EMBEDDING / SEARCH ══
  {
    service: 'voyage',
    displayName: 'Voyage AI',
    module: 'EMBEDDING',
    blurb: 'Retrieval-tuned embeddings; the best free option for search quality.',
    tier: 'free',
    costNote: 'Free tier: 200M tokens.',
    fields: [API_KEY('pa-…')],
    envVar: 'VOYAGE_API_KEY',
    docsUrl: 'https://dash.voyageai.com',
    models: ['voyage-3', 'voyage-3-lite'],
  },
  {
    service: 'jina',
    displayName: 'Jina AI',
    module: 'EMBEDDING',
    blurb: 'Multilingual embeddings with a no-signup trial key.',
    tier: 'free',
    costNote: 'Free tier: 1M tokens, no card required.',
    fields: [API_KEY('jina_…')],
    envVar: 'JINA_API_KEY',
    docsUrl: 'https://jina.ai/embeddings',
  },
  {
    service: 'local_bge',
    displayName: 'Local BGE / e5 (asr container)',
    module: 'EMBEDDING',
    blurb: 'Sentence-transformers running locally. Keeps transcripts private.',
    tier: 'local',
    costNote: 'Free. Adds ~500 MB to the asr image.',
    fields: [],
    docsUrl: 'https://huggingface.co/BAAI/bge-m3',
    localOnly: true,
  },
  {
    service: 'openai_embeddings',
    displayName: 'OpenAI embeddings',
    module: 'EMBEDDING',
    sharesKeyWith: 'openai',
    blurb: 'text-embedding-3, the common baseline for pgvector setups.',
    tier: 'paid',
    costNote: '~$0.02/M tokens for the small model.',
    fields: [API_KEY('sk-…')],
    envVar: 'OPENAI_API_KEY',
    docsUrl: 'https://platform.openai.com/api-keys',
    verify: openaiCompatibleVerify('https://api.openai.com/v1'),
    models: ['text-embedding-3-small', 'text-embedding-3-large'],
  },

  // ═══════════════════════════════════════════════════════ INTEGRATIONS ══
  {
    service: 'notion',
    displayName: 'Notion',
    module: 'INTEGRATION',
    blurb: 'Internal integration token for creating task pages.',
    tier: 'free',
    costNote: 'Free with any Notion plan.',
    fields: [API_KEY('ntn_…', 'Create an internal integration, then share your task database with it.')],
    envVar: 'NOTION_API_KEY',
    docsUrl: 'https://www.notion.so/my-integrations',
    verify: {
      url: 'https://api.notion.com/v1/users/me',
      auth: 'bearer',
      extraHeaders: { 'Notion-Version': '2022-06-28' },
    },
  },
  {
    service: 'slack',
    displayName: 'Slack',
    module: 'INTEGRATION',
    blurb: 'Bot token for scheduled reminders and channel messages.',
    tier: 'free',
    costNote: 'Free with any Slack workspace.',
    fields: [API_KEY('xoxb-…')],
    envVar: 'SLACK_BOT_TOKEN',
    docsUrl: 'https://api.slack.com/apps',
    verify: { url: 'https://slack.com/api/auth.test', auth: 'bearer' },
  },
  {
    service: 'google',
    displayName: 'Google OAuth app (Calendar + Gmail)',
    module: 'INTEGRATION',
    blurb: 'Client id and secret for the OAuth app. Each user then grants consent separately.',
    tier: 'free',
    costNote: 'Free. Calendar and Gmail APIs have no per-call charge.',
    fields: [
      {
        key: 'clientId',
        label: 'Client ID',
        placeholder: '…apps.googleusercontent.com',
        secret: false,
        required: true,
      },
      { key: 'clientSecret', label: 'Client secret', placeholder: 'GOCSPX-…', secret: true, required: true },
    ],
    envVar: 'GOOGLE_CLIENT_ID',
    docsUrl: 'https://console.cloud.google.com/apis/credentials',
  },
  {
    service: 'sendgrid',
    displayName: 'SendGrid',
    module: 'INTEGRATION',
    blurb:
      'Sends as the organisation from a verified domain — no per-user consent, works ' +
      'unattended. Cannot save drafts; Gmail handles those.',
    tier: 'freemium',
    costNote: 'Free tier: 100 emails/day. Also set SENDGRID_FROM_EMAIL to a verified sender.',
    fields: [
      API_KEY('SG.…', 'Needs at least the mail.send scope.'),
    ],
    envVar: 'SENDGRID_API_KEY',
    docsUrl: 'https://app.sendgrid.com/settings/api_keys',
    verify: { url: 'https://api.sendgrid.com/v3/scopes', auth: 'bearer' },
  },
]

export function findService(service: string): ServiceSpec | undefined {
  return CATALOG.find((s) => s.service === service)
}

export const TIER_META: Record<PricingTier, { label: string; tone: 'success' | 'accent' | 'muted' | 'warn' }> = {
  free: { label: 'Free tier', tone: 'success' },
  local: { label: 'Local / free', tone: 'accent' },
  freemium: { label: 'Free credit', tone: 'warn' },
  paid: { label: 'Paid', tone: 'muted' },
}

/** Free and local first, then freemium, then paid — the order the UI renders. */
const TIER_RANK: Record<PricingTier, number> = { free: 0, local: 1, freemium: 2, paid: 3 }

export function servicesForModule(module: CredentialModule): ServiceSpec[] {
  return CATALOG.filter((s) => s.module === module).sort(
    (a, b) => TIER_RANK[a.tier] - TIER_RANK[b.tier],
  )
}

export const MODULE_META: Record<CredentialModule, { title: string; blurb: string; icon: string }> = {
  TRANSCRIPTION: {
    title: 'Transcription',
    blurb: 'Speech to text with word timings and speaker labels.',
    icon: 'bi-soundwave',
  },
  EXTRACTION: {
    title: 'Action extraction',
    blurb: 'Turns a transcript into structured, owned, dated action items.',
    icon: 'bi-diagram-3',
  },
  TRANSLATION: {
    title: 'Translation',
    blurb: 'Reads a transcript back in another language.',
    icon: 'bi-translate',
  },
  EMBEDDING: {
    title: 'Semantic search',
    blurb: 'Embeddings for meaning-based transcript search.',
    icon: 'bi-search',
  },
  INTEGRATION: {
    title: 'Integrations',
    blurb: 'Where approved actions are actually executed.',
    icon: 'bi-plug',
  },
}

export const MODULE_ORDER: readonly CredentialModule[] = [
  'TRANSCRIPTION',
  'EXTRACTION',
  'TRANSLATION',
  'EMBEDDING',
  'INTEGRATION',
]


/**
 * Services that share a key with `service`, in either direction — so the UI can say
 * "also used for action extraction" on the entry the key was actually entered against.
 */
export function siblingsOf(service: string): ServiceSpec[] {
  const spec = findService(service)
  const primary = spec?.sharesKeyWith ?? service
  return CATALOG.filter(
    (s) => s.service !== service && (s.service === primary || s.sharesKeyWith === primary),
  )
}
