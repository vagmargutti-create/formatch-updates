# FORMATCH

Do reconhecimento ao descarte, pronto para diagramação.

Versão 0.9.6 para Windows 11. Ao abrir o programa, escolha `DISTRIBUIR` ou
`DESCARTAR`; somente o módulo escolhido será aberto. O módulo de distribuição
usa as fotos da pasta
`Reconhecimento` como cadastro facial. O nome do arquivo é o número do álbum:

```text
Reconhecimento/004.JPG -> DISTRIBUÍDOS/004/
```

## Regras implementadas

- O número impresso na ficha é ignorado.
- Uma foto pode ser copiada para vários formandos.
- Rostos não cadastrados são ignorados quando há ao menos um formando na foto.
- Quando nenhum formando é reconhecido, a foto vai para `SEM IDENTIFICAÇÃO`.
- Os originais nunca são movidos ou apagados.
- Álbuns com menos de 12 fotos recebem aviso; não são movidos automaticamente.
- Pastas e fotos dentro de subpastas também são lidas.
- O processamento pode ser interrompido com segurança.

## Instalação no Windows

1. Instale Python 3.11 (64 bits).
2. Atualize o driver da NVIDIA RTX 3060.
3. Instale as Microsoft C++ Build Tools 2022 com a carga
   `Desenvolvimento para desktop com C++`.
4. Dê duplo clique em `instalar.bat`.
5. Depois, abra `iniciar.bat`.

A versão 0.9.6 instala CUDA e cuDNN no próprio ambiente do programa e carrega
essas bibliotecas antes de iniciar o reconhecimento. Não é necessário instalar
o CUDA Toolkit completo separadamente.

Na primeira execução, o modelo facial pode precisar ser baixado. Para a versão
final será preparado um instalador que já inclui as dependências necessárias.

## Estrutura da saída

```text
Saída/
├── DISTRIBUÍDOS/
│   ├── 004/
│   ├── 007/
│   └── ...
└── SEM IDENTIFICAÇÃO/
```

## Fluxo da versão 0.9.6

1. `INICIAR ANÁLISE` reconhece e salva um projeto, sem copiar os álbuns.
2. A revisão separa `Reconhecidos` e `Sem ID` em abas.
3. Em `Reconhecidos`, primeiro aparecem os álbuns; as fotos abrem ao clicar no número.
4. Anterior e Próxima sempre seguem a ordem numérica original de Eventos.
5. Cada rosto exibe os cinco formandos mais parecidos.
6. Identificações podem ser adicionadas ou removidas manualmente.
7. Um novo ID pode ser criado a partir de uma foto e revarrer os sem ID.
8. `EXPORTAR PASTAS` cria `DISTRIBUÍDOS` e `SEM IDENTIFICAÇÃO` somente no final.
9. Na exportação, a opção de cópia de segurança cria um espelho dos álbuns em
   `OUTROS/CÓPIA DE SEGURANÇA`, incluindo IDs `SI001`, `SI002` e seguintes.
10. As fotos de `SEM IDENTIFICAÇÃO` nunca entram na cópia de segurança.

## Atualizações automáticas

Ao abrir, o FORMATCH consulta o endereço configurado em `update_config.json`.
Quando uma versão mais recente estiver publicada, oferece `ATUALIZAR AGORA` e
`MAIS TARDE`. O pacote é baixado por HTTPS, conferido pelo SHA-256 e aplicado
somente depois que o programa fecha. A versão anterior é preservada para
recuperação se a instalação não for concluída.

O projeto fica em `projeto_distribuicao.sqlite3` e permite continuar uma análise
ou revisão interrompida. A interface mostra velocidade e tempo restante.

## Segurança

O processamento é local. Ainda assim, as fotos de cadastro representam dados
biométricos e devem ter acesso controlado e prazo de retenção definido.

## Seleção e descarte

Na tela inicial, o botão `DESCARTAR` abre somente a seleção e descarte e permite escolher a pasta
individual de um formando, como `ALBUNS/001`. O programa compara somente as
fotos desse álbum, detecta arquivos idênticos ou muito semelhantes e pontua
nitidez, exposição, contraste e resolução.

- A melhor foto de cada sequência permanece no álbum.
- As demais são movidas para `OUTROS/DESCARTE POR ÁLBUM/DESC 001`, por exemplo.
- Nada é apagado.
- O programa não cria cópia de segurança.
- O último movimento pode ser desfeito pela própria tela.
- O resultado é exibido para conferência antes da confirmação.
- Sequências consecutivas podem ser agrupadas mesmo com mudança de enquadramento.
- A escolha automática usa somente qualidade técnica: nitidez, exposição, contraste e resolução.
- Olhos, sorriso e expressão não influenciam mais o descarte automático.
- Mudanças relevantes na direção dos rostos criam poses diferentes e são preservadas.
- Cada sugestão possui uma caixa que pode ser desmarcada antes de mover os arquivos.
- Ao clicar em uma sugestão, a fotografia aparece grande ao lado direito.
- A borda verde indica `MANTER` e a vermelha indica `DESCARTAR`.
- Distribuição e descarte exibem apenas o tempo restante estimado, sem velocidade por foto.

## Revisão e atalhos

- A revisão possui a aba `Possível fundo` para rostos reconhecidos muito menores
  do que o rosto principal da fotografia.
- `A` copia para a foto atual a identificação da foto anterior e `D` copia da próxima.
- As teclas `1` a `5` escolhem um dos cinco candidatos e `Delete` remove uma identificação.
