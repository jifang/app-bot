import fs from "node:fs";
import process from "node:process";
import COS from "cos-nodejs-sdk-v5";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const params = JSON.parse(input);
const cert = params.apply.tempCertificate;
if (!cert?.secretId || !cert?.secretKey || !cert?.token) {
  throw new Error("VOD response has no temporary COS credentials");
}

const cos = new COS({
  getAuthorization(_options, callback) {
    callback({
      TmpSecretId: cert.secretId,
      TmpSecretKey: cert.secretKey,
      SecurityToken: cert.token,
      StartTime: params.apply.timestamp,
      ExpiredTime: cert.expiredTime,
    });
  },
});

const bucket = `${params.apply.storageBucket}-${params.apply.storageAppId}`;
const region = params.apply.storageRegionV5;
const key = params.apply.video?.storagePath;
if (!bucket || !region || !key) throw new Error("VOD response is missing COS target details");
if (!fs.statSync(params.videoPath).isFile()) throw new Error("videoPath is not a file");

await new Promise((resolve, reject) => {
  cos.uploadFile({
    Bucket: bucket,
    Region: region,
    Key: key,
    FilePath: params.videoPath,
    SliceSize: 5 * 1024 * 1024,
    ChunkSize: 1 * 1024 * 1024,
    AsyncLimit: 2,
    onProgress(info) {
      const percent = Math.floor(Number(info.percent || 0) * 100);
      process.stderr.write(`\r视频上传 ${Math.min(percent, 100)}%`);
    },
  }, (error) => {
    process.stderr.write("\n");
    if (error) reject(error);
    else resolve();
  });
});

process.stdout.write(JSON.stringify({ uploaded: true, key }));
