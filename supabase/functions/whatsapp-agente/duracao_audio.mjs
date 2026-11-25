// Duração calculada do arquivo, nunca de texto/metadata enviado pelo cliente.
export function medirDuracao(bytes,mime) {
 const v=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
 const texto=(a,n)=>String.fromCharCode(...bytes.subarray(a,a+n));
 if(mime==='audio/wav') {
  if(bytes.length<44||texto(0,4)!=='RIFF'||texto(8,4)!=='WAVE')throw new Error('wav_invalido');
  let p=12,taxa=0,dados=0;
  while(p+8<=bytes.length){const nome=texto(p,4),n=v.getUint32(p+4,true);if(p+8+n>bytes.length)throw new Error('wav_truncado');
   if(nome==='fmt '){if(n<16||v.getUint16(p+8,true)!==1)throw new Error('wav_codec');taxa=v.getUint32(p+16,true);}
   if(nome==='data')dados+=n;p+=8+n+(n%2);
  }
  if(!taxa||!dados)throw new Error('wav_sem_audio');return dados/taxa;
 }
 if(mime!=='audio/ogg')throw new Error('formato_duracao_nao_habilitado');
 let p=0,serial=null,seq=0,preSkip=null,granulo=0n,fim=false,pacote=[],amostras=0,tags=false;
 while(p<bytes.length){
  if(p+27>bytes.length||texto(p,4)!=='OggS'||bytes[p+4]!==0)throw new Error('ogg_invalido');
  const n=bytes[p+26];if(p+27+n>bytes.length)throw new Error('ogg_truncado');
  let tam=0;for(let i=0;i<n;i++)tam+=bytes[p+27+i];const inicio=p+27+n,final=inicio+tam;
  if(final>bytes.length)throw new Error('ogg_truncado');
  const s=v.getUint32(p+14,true),q=v.getUint32(p+18,true);
  if(serial===null){serial=s;seq=q;if(!(bytes[p+5]&2)||tam<19||texto(inicio,8)!=='OpusHead'||bytes[inicio+8]!==1||![1,2].includes(bytes[inicio+9]))throw new Error('opus_cabecalho');preSkip=v.getUint16(inicio+10,true);}
  if(s!==serial||q!==seq++)throw new Error('ogg_fluxo_descontinuo');
  if(Boolean(bytes[p+5]&1)!==Boolean(pacote.length))throw new Error('ogg_continuacao_invalida');
  let off=inicio;
  for(let i=0;i<n;i++){
   const seg=bytes[p+27+i];pacote.push(...bytes.subarray(off,off+seg));off+=seg;
   if(seg<255){
    const assinatura=String.fromCharCode(...pacote.slice(0,8));
    if(assinatura==='OpusTags')tags=true;
    else if(assinatura!=='OpusHead'){
     if(!pacote.length)throw new Error('opus_pacote_vazio');
     const toc=pacote[0],q=(toc>>3)&3;
     const frame=(toc&128)?120*(1<<q):((toc&96)===96?(toc&8?960:480):(q===3?2880:480*(1<<q)));
     if((toc&3)===3&&pacote.length<2)throw new Error('opus_pacote_truncado');
     const count=(toc&3)===0?1:((toc&3)===3?pacote[1]&63:2);
     if(!count||frame*count>5760)throw new Error('opus_duracao_pacote');
     amostras+=frame*count;
    }
    pacote=[];
   }
  }
  const g=v.getBigUint64(p+6,true);if(g!==0xffffffffffffffffn){if(g<granulo)throw new Error('ogg_granulo');granulo=g;}
  if(bytes[p+5]&4){fim=true;if(final!==bytes.length)throw new Error('ogg_fluxos_multiplos');}
  p=final;
 }
 if(pacote.length||(!fim&&!tags)||preSkip===null||granulo<=BigInt(preSkip)||granulo>172800000n||!amostras)throw new Error('ogg_duracao_nao_confirmada');
 // Alguns áudios recebidos não têm EOS. Nunca subestimar: usar também a soma dos frames Opus.
 return Math.max(Number(granulo-BigInt(preSkip)),amostras)/48000;
}

