# Ponto Control iD — aplicação web local

MVP para importar AFD do relógio **Control iD iDClass Bio Prox**, cadastrar colaboradores, configurar jornada individual e apurar horas trabalhadas / extras.

## Como executar

### Windows
1. Instale Python 3.11 ou superior.
2. Abra a pasta do projeto no Prompt/PowerShell.
3. Execute `run_windows.bat`.
4. Abra `http://127.0.0.1:8000`.

### macOS / Linux
```bash
chmod +x run_mac_linux.sh
./run_mac_linux.sh
```
Depois abra `http://127.0.0.1:8000`.

## Primeiro uso
1. Vá em **Importar AFD** e envie o TXT/AFD exportado pelo relógio.
2. Vá em **Colaboradores** e ajuste a jornada de cada pessoa.
3. Vá em **Configurações** e defina tolerância, percentuais, banco de horas, intervalo e feriados.
4. Use o **Dashboard** e o relatório individual para conferir a apuração.

## Regras da versão 0.1
- Registros AFD tipo `3`: tratados como batidas.
- Registros AFD tipo `5`: usados para atualizar o nome/identificador do colaborador.
- Batidas são pareadas por ordem cronológica: 1-2, 3-4 etc.
- Quantidade ímpar de batidas gera **Marcação incompleta** e o dia não entra nos totais de HE/débito até conferência.
- A diferença entre horas trabalhadas e carga prevista só é zerada quando estiver dentro da tolerância diária configurada.
- Domingo/feriado é classificado na coluna de HE 100%; demais extras ficam na coluna de HE 50% nesta primeira versão. Os percentuais são armazenados nas configurações para evolução da folha/cálculo financeiro.
- O AFD original é preservado em `uploads/` e cada batida guarda a linha bruta e hash para evitar duplicação.
- A faixa noturna já é configurável, mas o adicional noturno ainda não é calculado automaticamente na versão 0.1.

## Importante
Este MVP é uma ferramenta de apuração e conferência. Regras trabalhistas específicas da empresa, convenção coletiva, escalas, compensações, ausências justificadas, DSR, adicional noturno com hora reduzida e fechamento de folha devem ser validados antes de uso como cálculo oficial de folha.


## Compatibilidade macOS/Linux
Esta versão usa dependências compatíveis com Python 3.9 ou superior.
