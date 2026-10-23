// Máscaras simples PT-BR: .cpf  .tel
function mascaraCPF(v) {
  v = v.replace(/\D/g, '').slice(0, 11);
  return v.replace(/(\d{3})(\d)/, '$1.$2').replace(/(\d{3})(\d)/, '$1.$2').replace(/(\d{3})(\d{1,2})$/, '$1-$2');
}
function mascaraTel(v) {
  v = v.replace(/\D/g, '').slice(0, 11);
  if (v.length > 10) return v.replace(/(\d{2})(\d{5})(\d{0,4})/, '($1) $2-$3');
  if (v.length > 6) return v.replace(/(\d{2})(\d{4})(\d{0,4})/, '($1) $2-$3');
  if (v.length > 2) return v.replace(/(\d{2})(\d*)/, '($1) $2');
  return v;
}
document.addEventListener('input', e => {
  if (e.target.classList.contains('cpf')) e.target.value = mascaraCPF(e.target.value);
  if (e.target.classList.contains('tel')) e.target.value = mascaraTel(e.target.value);
});
