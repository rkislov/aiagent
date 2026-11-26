import React from 'react';
export default function ArtifactLinks({output}){
 const file=output?.file;
 if(!file||!/^[a-f0-9]{32}$/.test(file.id))return null;
 const image=file.extension==='png';
 const url=image?'/api/images/'+file.id:'/api/files/'+file.id+'/download';
 return <div className="artifact-result">{image&&<img src={url} alt="Сгенерированное изображение"/>}<a className="button secondary" href={url} download={file.name}>Скачать {file.name}</a></div>
}
