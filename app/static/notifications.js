// Shared, non-blocking confirmations for both terminal screens.
let activeConfirmation = false;
function confirmAction({title, message, confirmLabel = 'Onayla', acknowledgement = '', details = []}) {
  if (activeConfirmation) return Promise.resolve(false);
  activeConfirmation = true;
  return new Promise(resolve => {
    const previousFocus = document.activeElement;
    const dialog = document.createElement('dialog');
    dialog.className = 'trade-confirm';
    dialog.setAttribute('aria-labelledby', 'trade-confirm-title');
    dialog.setAttribute('aria-describedby', 'trade-confirm-message');
    dialog.innerHTML = `<form method="dialog">
      <div class="trade-confirm-heading"><span class="trade-confirm-icon" aria-hidden="true">!</span><h2 id="trade-confirm-title"></h2></div>
      <p id="trade-confirm-message"></p><dl class="trade-confirm-details"></dl>
      <label class="trade-confirm-check"><input type="checkbox"><span></span></label>
      <div class="trade-confirm-actions"><button type="button" class="trade-confirm-cancel">Vazgeç</button><button type="submit" class="trade-confirm-accept"></button></div>
    </form>`;
    dialog.querySelector('h2').textContent = title;
    dialog.querySelector('p').textContent = message;
    const list = dialog.querySelector('dl');
    list.hidden = !details.length;
    details.forEach(([label, value]) => {
      const term = document.createElement('dt'), definition = document.createElement('dd');
      term.textContent = label; definition.textContent = String(value);
      list.append(term, definition);
    });
    const check = dialog.querySelector('input'), label = dialog.querySelector('label');
    label.hidden = !acknowledgement;
    label.querySelector('span').textContent = acknowledgement;
    const accept = dialog.querySelector('.trade-confirm-accept');
    accept.textContent = confirmLabel;
    accept.disabled = !!acknowledgement;
    check.addEventListener('change', () => { accept.disabled = !check.checked; });
    let accepted = false;
    dialog.querySelector('form').addEventListener('submit', event => {
      event.preventDefault();
      if (accept.disabled) return;
      accepted = true; dialog.close();
    });
    dialog.querySelector('.trade-confirm-cancel').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => {
      dialog.remove(); activeConfirmation = false;
      if (previousFocus?.isConnected) previousFocus.focus();
      resolve(accepted);
    }, {once: true});
    document.body.appendChild(dialog);
    dialog.showModal();
    dialog.querySelector('.trade-confirm-cancel').focus();
  });
}
