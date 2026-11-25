// Parser real, sem executar conteúdo ativo do documento. Dependência injetada e fixada no servidor.
export function criarValidadorPdf(PDFDocument){
 return async function validarPdf(bytes){
  if(!(bytes instanceof Uint8Array)||bytes.length<8||bytes.length>2097152)throw Error('pdf_fora_limite');
  const doc=await PDFDocument.load(bytes,{ignoreEncryption:false,updateMetadata:false,throwOnInvalidObject:true});
  const paginas=doc.getPageCount();
  if(doc.isEncrypted||!Number.isInteger(paginas)||paginas<1||paginas>10)throw Error('pdf_fora_limite');
  return {valido:true,criptografado:false,paginas};
 };
}
