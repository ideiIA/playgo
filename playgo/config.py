import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

RAIZ = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=RAIZ / os.environ.get("PLAYGO_ENV_FILE", ".env"), env_prefix="PLAYGO_", extra="ignore")

    database_url: str = "postgresql+psycopg://playgo@localhost:5434/playgo"

    # Sessão do site e token do app: chave que assina os dois (em branco = gerada em .chave_sessao)
    chave_sessao: str = ""
    dias_token_app: int = 30

    fuso: str = "America/Campo_Grande"
    # Onde o mapa abre para quem ainda não informou a localização (Campo Grande, MS)
    latitude_padrao: float = -20.4697
    longitude_padrao: float = -54.6201

    # Descoberta
    raio_padrao_km: int = 10
    limite_resultados: int = 60
    # "Falta gente" some da vitrine depois que o jogo começou há mais que isso
    tolerancia_inicio_min: int = 30

    # Publicações e mídia
    pasta_uploads: Path = RAIZ / "uploads"
    max_foto_mb: int = 10
    max_video_mb: int = 50
    max_pdf_mb: int = 15  # regulamento em PDF
    max_midias_post: int = 4
    max_texto_post: int = 2000
    janela_exclusao_min: int = 60  # o autor só exclui na primeira hora; depois, só moderador/admin oculta
    limite_posts_10min: int = 10
    denuncias_para_analise: int = 3  # denúncias que mandam o conteúdo para a análise do admin

    # Hospedagem. No Vercel (variável VERCEL) o servidor é serverless: sem disco, sem threads, sem DDL a cada partida.
    serverless: bool = False
    auto_migrar: bool = True  # cria/atualiza as tabelas ao subir (desligado no serverless)
    armazenamento: str = ""  # "local" força o disco; em branco = Supabase se houver URL e chave
    supabase_url: str = ""  # https://<ref>.supabase.co
    supabase_service_key: str = ""  # chave de serviço (secreta): só no servidor, nunca no navegador
    supabase_bucket: str = "playgo-midia"
    cron_secret: str = ""  # protege /api/v1/cron/ciclo (no Vercel: variável CRON_SECRET)

    # Login com Google (OAuth 2.0). Sem client id/secret o botão não aparece. url_publica só se o endereço detectado estiver errado.
    google_client_id: str = ""
    google_client_secret: str = ""
    url_publica: str = ""  # ex.: https://palygo.vercel.app

    # Instagram do PlayGo (@playgo.sports): app da Meta (developers.facebook.com). A conta se conecta por botão, sem colar token.
    instagram_app_id: str = ""
    instagram_app_secret: str = ""
    instagram_versao: str = "v21.0"
    youtube_client_id: str = ""  # OAuth do canal oficial do PlayGo (docs/youtube.md)
    youtube_client_secret: str = ""
    livekit_url: str = ""  # câmera pelo navegador (LiveKit Cloud): wss://<projeto>.livekit.cloud — docs/youtube.md
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    youtube_max_simultaneas: int = 4  # transmissões preparadas ou no ar ao mesmo tempo (protege a cota da API e a banda do local)

    # Planos e cobrança (Asaas). Sem chave, o app funciona e o admin libera planos à mão.
    teste_dias: int = 30
    tolerancia_dias: int = 7
    asaas_api_key: str = ""
    asaas_ambiente: str = "sandbox"  # sandbox | producao
    asaas_webhook_token: str = ""  # o mesmo token cadastrado no webhook do Asaas

    # Moderação por IA. Provedor: "claude" ou "gemini"; em branco = o que tiver chave preenchida.
    ia_provedor: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    modelo_moderacao: str = ""  # em branco = modelo barato padrão do provedor
    # Foto/vídeo quando não há IA que consiga analisar: "revisar" (admin avalia) ou "liberar"
    moderacao_midia_sem_ia: str = "revisar"

    # LGPD / Marco Civil
    controlador_nome: str = "PlayGo (razão social a definir)"
    contato_privacidade: str = "privacidade@playgo.invalid"
    retencao_registros_dias: int = 365  # prazo de guarda dos registros de interação (confirmar com o jurídico)
    confiar_proxy: bool = False  # lê o IP de X-Forwarded-For (só atrás de proxy de confiança)
    cookie_seguro: bool = False  # marca o cookie de sessão como Secure (ligue em produção com HTTPS)

    # Agendador: lembretes e reposição. A interface também dispara.
    agendador_na_web: bool = True
    intervalo_agendador_s: int = 60
    lembrete_antecedencia_min: int = 120


    @property
    def em_serverless(self) -> bool:
        return self.serverless or bool(os.environ.get("VERCEL"))

    @property
    def migrar_ao_subir(self) -> bool:
        return self.auto_migrar and not self.em_serverless

    @property
    def segredo_cron(self) -> str:
        return self.cron_secret or os.environ.get("CRON_SECRET", "")

    # O Vercel limita o corpo da requisição a ~4,5 MB; nesse ambiente o teto efetivo é menor que o configurado.
    @property
    def limite_foto_mb(self) -> int:
        return min(self.max_foto_mb, 4) if self.em_serverless else self.max_foto_mb

    @property
    def limite_pdf_mb(self) -> int:
        return min(self.max_pdf_mb, 4) if self.em_serverless else self.max_pdf_mb

    @property
    def limite_video_mb(self) -> int:
        return min(self.max_video_mb, 4) if self.em_serverless else self.max_video_mb

    @property
    def provedor_ia(self) -> str:
        """'claude', 'gemini' ou '' (sem IA configurada)."""
        if self.ia_provedor in ("claude", "gemini"):
            return self.ia_provedor if (self.anthropic_api_key if self.ia_provedor == "claude" else self.gemini_api_key) else ""
        return "claude" if self.anthropic_api_key else "gemini" if self.gemini_api_key else ""


settings = Settings()
