// A visible role hint; the API independently enforces every permission.
document.addEventListener('DOMContentLoaded', async () => {
  try {
    const response = await fetch('/api/auth/me');
    if (!response.ok) return;
    const {username, role} = await response.json();
    const english = window.MT5I18n?.language?.() === 'en';
    const names = english
      ? {ADMIN:'Admin',TRADER:'Trader',VIEWER:'View only'}
      : {ADMIN:'Yönetici',TRADER:'İşlemci',VIEWER:'Sadece izle'};
    const badge = document.getElementById('operator-role');
    if (badge) {
      badge.textContent = `${username} · ${names[role] || role}`;
      badge.title = english ? 'Panel permissions' : 'Panel yetkileri';
    }
    const adminActions = ['toggleAIAutopilotModal', 'saveAIAutopilot', 'toggleBot',
      'toggleBotSettingsModal', 'saveBotSettings', 'resumeTrading', 'submitLogin'];
    const tradeActions = ['submitOrder', 'executeCurrentAdvice', 'placePendingOrder',
      'savePositionStops', 'partialClosePosition', 'closePosition'];
    document.querySelectorAll('button[onclick]').forEach(button => {
      const handler = button.getAttribute('onclick') || '';
      const blocked = (role !== 'ADMIN' && adminActions.some(name => handler.startsWith(name + '('))) ||
        (role === 'VIEWER' && tradeActions.some(name => handler.startsWith(name + '(')));
      if (blocked) {
        button.disabled = true;
        button.title = english ? 'Your account cannot perform this action' : 'Bu işlem için yetkiniz yok';
        button.classList.add('opacity-50', 'cursor-not-allowed');
      }
    });
  } catch (error) { console.debug('Operator role unavailable', error); }
});
