import { useEffect, useState } from 'react';
import edvidLogo from '../../assets/preview/edvid-logo-white.png';
import type { DesktopInfo, RuntimeCheck } from './shared';

const labels: Record<RuntimeCheck['name'], string> = {
  node: 'Node.js',
  npm: 'npm',
  ffmpeg: 'FFmpeg',
  ffprobe: 'FFprobe',
  uv: 'uv',
  'yt-dlp': 'yt-dlp',
  python: 'Python',
  whisperx: 'WhisperX',
};

const sourceLabels: Record<RuntimeCheck['source'], string> = {
  bundled: 'interno',
  system: 'sistema (dev)',
  missing: 'pendente',
};

export function App() {
  const [desktopInfo, setDesktopInfo] = useState<DesktopInfo | null>(null);
  const [runtimes, setRuntimes] = useState<RuntimeCheck[]>([]);
  const [projectDirectory, setProjectDirectory] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);

  async function refreshRuntimes() {
    setChecking(true);
    try {
      setRuntimes(await window.edvidDesktop.checkRuntimes());
    } finally {
      setChecking(false);
    }
  }

  async function chooseProjectDirectory() {
    const selected = await window.edvidDesktop.selectProjectDirectory();
    if (selected) setProjectDirectory(selected);
  }

  useEffect(() => {
    void window.edvidDesktop.getDesktopInfo().then(setDesktopInfo);
    void refreshRuntimes();
  }, []);

  return (
    <div className="desktop-shell">
      <header className="glass desktop-header">
        <div className="logo-chip"><img src={edvidLogo} alt="Edvid" /></div>
        <div className="head-info">
          <div className="project-name">Edvid Desktop</div>
          <div className="state-message">Editor de video por conversa — ambiente local</div>
        </div>
        <div className="head-actions">
          <span className="pill saved">Fundacao 0.1</span>
        </div>
      </header>

      <nav className="tabs" aria-label="Navegacao principal">
        <button className="tab active"><span className="tab-num">EDVID</span> Inicio</button>
        <button className="tab" disabled>Projetos</button>
        <button className="tab" disabled>Configuracoes</button>
      </nav>

      <main className="desktop-main">
        <section className="setup desktop-setup">
          <div className="setup-inner desktop-content">
            <div className="setup-head">
              <h2>Onde vamos criar a proxima edicao?</h2>
              <p className="state-message desktop-intro">
                O aplicativo e o projeto de video ficam separados. O Edvid trabalha
                apenas dentro da pasta escolhida e preserva o material original.
              </p>
            </div>

            <div className="setup-group">
              <div className="group-head"><span className="group-title">Pasta do projeto</span></div>
              <div className={`project-selector glass ${projectDirectory ? 'selected' : ''}`}>
                <div className="project-selector-copy">
                  <span className={`runtime-dot ${projectDirectory ? 'ready' : ''}`} />
                  <span className={projectDirectory ? 'project-path' : 'project-path dim'}>
                    {projectDirectory ?? 'Nenhuma pasta selecionada'}
                  </span>
                </div>
                <button className="btn primary" onClick={chooseProjectDirectory}>Escolher pasta</button>
              </div>
            </div>

            <div className="desktop-grid">
              <section className="runtime-panel glass">
                <div className="panel-head">
                  <div>
                    <div className="group-title">Runtimes do Edvid</div>
                    <h3>Dependencias verificadas</h3>
                  </div>
                  <button className="btn ghost small" onClick={refreshRuntimes} disabled={checking}>
                    {checking ? 'Verificando...' : 'Verificar novamente'}
                  </button>
                </div>

                <div className="runtime-list">
                  {runtimes.map((runtime) => (
                    <div className="runtime-row" key={runtime.name}>
                      <span className={`runtime-dot ${runtime.available ? 'ready' : 'missing'}`} />
                      <strong>{labels[runtime.name]}</strong>
                      <span className="runtime-version" title={runtime.executablePath ?? undefined}>
                        {runtime.version ?? runtime.error ?? 'Nao encontrado'}
                      </span>
                      <span className={`runtime-source ${runtime.source}`}>{sourceLabels[runtime.source]}</span>
                    </div>
                  ))}
                </div>
                <p className="setup-summary runtime-summary">
                  Versoes esperadas sao fixadas no manifesto. Em producao, o Edvid
                  nao usa silenciosamente programas instalados pelo usuario.
                </p>
              </section>

              <aside className="system-panel glass">
                <div className="group-title">Aplicativo</div>
                <h3>Shell Electron ativo</h3>
                <dl>
                  <div><dt>Plataforma</dt><dd>{desktopInfo?.platform ?? '...'}</dd></div>
                  <div><dt>Arquitetura</dt><dd>{desktopInfo?.arch ?? '...'}</dd></div>
                  <div><dt>Electron</dt><dd>{desktopInfo?.electronVersion ?? '...'}</dd></div>
                  <div><dt>Node do Electron</dt><dd>{desktopInfo?.embeddedNodeVersion ?? '...'}</dd></div>
                </dl>
                <div className="system-note">
                  A interface usa o mesmo design system da timeline do Edvid e se
                  comunica com o sistema por uma ponte isolada.
                </div>
              </aside>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}
