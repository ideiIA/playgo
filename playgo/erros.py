class ErroNegocio(Exception):
    """Regra de negócio violada. A mensagem já está em português e vai direto para a tela."""


class NaoEncontrado(ErroNegocio):
    pass


class SemPermissao(ErroNegocio):
    pass


class PlanoNecessario(ErroNegocio):
    """O recurso pedido faz parte de um plano pago. A tela leva a pessoa a Meu plano (HTTP 402 na API)."""

    def __init__(self, plano: str, mensagem: str) -> None:
        super().__init__(mensagem)
        self.plano = plano


class TentativasDemais(ErroNegocio):
    """Muitas tentativas de entrada ou de cadastro vindas do mesmo lugar em pouco tempo (HTTP 429)."""
