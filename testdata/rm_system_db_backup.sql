-- MySQL dump 10.13  Distrib 8.0.40, for Linux (x86_64)
--
-- Host: localhost    Database: rm_system_db_backup
-- ------------------------------------------------------
-- Server version	8.0.40-0ubuntu0.22.04.1

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Table structure for table `activecontracts`
--

DROP TABLE IF EXISTS `activecontracts`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `activecontracts` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `contractName` text,
  `contractAddress` text,
  `status` text,
  `tokenIdentification` text,
  `contractType` text,
  `transactionHash` text,
  `blockNumber` bigint DEFAULT NULL,
  `blockHash` text,
  `incorporationDate` text,
  `expiryDate` text,
  `closeDate` text,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=2 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `activecontracts`
--

LOCK TABLES `activecontracts` WRITE;
/*!40000 ALTER TABLE `activecontracts` DISABLE KEYS */;
INSERT INTO `activecontracts` VALUES (1,'swap-tokenroom-usd','FUSBi3sQ9YFRs76wiMrDYmhqguPCXue8po','active','[\'usd\', \'tokenroom\']','continuos-event','0340a0e595cdb319b46f80e5f09fdcb84e4fe7ee64d09a590a2c5855a3f1d79c',6095603,'a6c7fcb9e0f70f7de5fdae998b79b9fb7edc8e643562eea921045973f97de11c','1681731341',NULL,NULL);
/*!40000 ALTER TABLE `activecontracts` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `contractAddressMapping`
--

DROP TABLE IF EXISTS `contractAddressMapping`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `contractAddressMapping` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `address` text,
  `addressType` text,
  `contractName` text,
  `contractAddress` text,
  `tokenAmount` float DEFAULT NULL,
  `transactionHash` text,
  `blockNumber` bigint DEFAULT NULL,
  `blockHash` text,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=2 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `contractAddressMapping`
--

LOCK TABLES `contractAddressMapping` WRITE;
/*!40000 ALTER TABLE `contractAddressMapping` DISABLE KEYS */;
INSERT INTO `contractAddressMapping` VALUES (1,'FUSBi3sQ9YFRs76wiMrDYmhqguPCXue8po','incorporation','swap-tokenroom-usd','FUSBi3sQ9YFRs76wiMrDYmhqguPCXue8po',NULL,'0340a0e595cdb319b46f80e5f09fdcb84e4fe7ee64d09a590a2c5855a3f1d79c',6095603,'a6c7fcb9e0f70f7de5fdae998b79b9fb7edc8e643562eea921045973f97de11c');
/*!40000 ALTER TABLE `contractAddressMapping` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `databaseTypeMapping`
--

DROP TABLE IF EXISTS `databaseTypeMapping`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `databaseTypeMapping` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `db_name` text,
  `db_type` text,
  `keyword` text,
  `object_format` text,
  `blockNumber` bigint DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=4 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `databaseTypeMapping`
--

LOCK TABLES `databaseTypeMapping` WRITE;
/*!40000 ALTER TABLE `databaseTypeMapping` DISABLE KEYS */;
INSERT INTO `databaseTypeMapping` VALUES (1,'usd','infinite-token','','{\"root_address\": \"F9rMGN3sgSLjKiUxYuSTevxPbd2M2m2Xgv\"}',5335297),(2,'tokenroom','token','','',6061868),(3,'swap-tokenroom-usd-FUSBi3sQ9YFRs76wiMrDYmhqguPCXue8po','smartcontract','','',6095603);
/*!40000 ALTER TABLE `databaseTypeMapping` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `rejectedContractTransactionHistory`
--

DROP TABLE IF EXISTS `rejectedContractTransactionHistory`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `rejectedContractTransactionHistory` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `transactionType` text,
  `transactionSubType` text,
  `contractName` text,
  `contractAddress` text,
  `sourceFloAddress` text,
  `destFloAddress` text,
  `transferAmount` float DEFAULT NULL,
  `blockNumber` bigint DEFAULT NULL,
  `blockHash` text,
  `time` bigint DEFAULT NULL,
  `transactionHash` text,
  `blockchainReference` text,
  `jsonData` text,
  `rejectComment` text,
  `parsedFloData` text,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `rejectedContractTransactionHistory`
--

LOCK TABLES `rejectedContractTransactionHistory` WRITE;
/*!40000 ALTER TABLE `rejectedContractTransactionHistory` DISABLE KEYS */;
/*!40000 ALTER TABLE `rejectedContractTransactionHistory` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `rejectedTransactionHistory`
--

DROP TABLE IF EXISTS `rejectedTransactionHistory`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `rejectedTransactionHistory` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `tokenIdentification` text,
  `sourceFloAddress` text,
  `destFloAddress` text,
  `transferAmount` float DEFAULT NULL,
  `blockNumber` bigint DEFAULT NULL,
  `blockHash` text,
  `time` bigint DEFAULT NULL,
  `transactionHash` text,
  `blockchainReference` text,
  `jsonData` text,
  `rejectComment` text,
  `transactionType` text,
  `parsedFloData` text,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `rejectedTransactionHistory`
--

LOCK TABLES `rejectedTransactionHistory` WRITE;
/*!40000 ALTER TABLE `rejectedTransactionHistory` DISABLE KEYS */;
/*!40000 ALTER TABLE `rejectedTransactionHistory` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `systemData`
--

DROP TABLE IF EXISTS `systemData`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `systemData` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `attribute` text,
  `value` text,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `systemData`
--

LOCK TABLES `systemData` WRITE;
/*!40000 ALTER TABLE `systemData` DISABLE KEYS */;
INSERT INTO `systemData` VALUES (1,'lastblockscanned','6334714'),(2,'lastblockscanned','3387922');
/*!40000 ALTER TABLE `systemData` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `time_actions`
--

DROP TABLE IF EXISTS `time_actions`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `time_actions` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `time` text,
  `activity` text,
  `status` text,
  `contractName` text,
  `contractAddress` text,
  `contractType` text,
  `tokens_db` text,
  `parsed_data` text,
  `transactionHash` text,
  `blockNumber` bigint DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `time_actions`
--

LOCK TABLES `time_actions` WRITE;
/*!40000 ALTER TABLE `time_actions` DISABLE KEYS */;
/*!40000 ALTER TABLE `time_actions` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `tokenAddressMapping`
--

DROP TABLE IF EXISTS `tokenAddressMapping`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `tokenAddressMapping` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `tokenAddress` text,
  `token` text,
  `transactionHash` text,
  `blockNumber` bigint DEFAULT NULL,
  `blockHash` text,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=25 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `tokenAddressMapping`
--

LOCK TABLES `tokenAddressMapping` WRITE;
/*!40000 ALTER TABLE `tokenAddressMapping` DISABLE KEYS */;
INSERT INTO `tokenAddressMapping` VALUES (1,'F9rMGN3sgSLjKiUxYuSTevxPbd2M2m2Xgv','usd','5f5c347c5048b7281e95b725cb64fd52f2dd17576d8d4c5c7d633692e7f8bdf9',5335297,'870c3c2a4453dbdbb8932fd08ea99b263cec3b058d2d70b2d0141c34475f0675'),(2,'es7CU9oZwHq4hk6QwnLY2HFp6MrBKE5bLo','tokenroom','f9d20f04835ed7cf83a9f79f8ecf4a90023101ab36dabebd608edeee850000d5',6061868,'23c438075a21d47f8ede6273bb1abe04a708c3945fe70cc1eb02a11a47a07efc'),(3,'F9UrruN9hPdbb4ky6nP4fDTfhPSezL1DXm','tokenroom','a8673d22c71fe75ba86a64ffc98d7689b77e9ca08036ef4d557452fd55ebe7fe',6064631,'7e904ed6d540079dcd01a70e174d6606a117cc0ba2fe82d311c19726217a9a2e'),(4,'FCCwEuutFg8tQd7RmXAaT7BiUw4jFYFPLT','tokenroom','1c8eace27fa334587b688b55cc89f5ef524424e74c64dc2284b92b4c94136d04',6064635,'8eae40c6e6428d4c249460f6936f3d8d7fa846c2cbd6261d92d125d0d57fe61d'),(5,'F5vbLtndEyaQrESdDd3J5BhnswGa8Te776','tokenroom','68fccad0c9d1924b972de9d6efc6a3dfe047b009b296c575c3f5aa40e41128f6',6068032,'1683d2ffc364d85493ab04f829ac082515a108f874c06aa4884626b565cd7fb2'),(6,'FHXom39DsEXdmDhmaXGFZbLUVFQFFyrqXf','tokenroom','c5ccca0905b88fa1608f422faaf644a39cb85d3700fe7b679a6dc7568e7372df',6068525,'fe6f9ec29bef05e0233b62920f27f9c3ee5c2efd9fc713cb66647488aa37792b'),(7,'FV9XbY6XZNUbEn81b7KE2U1CVxsUjZZTwc','tokenroom','ded32cd368edf8b0b43d104e979931a171ff88c8c0bb945ae4ceaa9d11c412e2',6068552,'e611e58ba0da1f82cea38bd670df09bb6596a9dcdbf1a1fc5dd29274e46b90b8'),(8,'F69G4hHjNzU7PM1MWxtY9nNXUGSSv6VjjP','tokenroom','8ced781d393bf3dfbdeff734efc4cebf9fd5cb67f370c75cd801a8f5b4e536ca',6068569,'bb9118af58ea25837a1bc960620ad9d8ebff618283a990d93c0530d8a422f8e7'),(9,'F6LwiQWQJ6Jef8miE1CHLGsqo2gRdw3tAE','tokenroom','4c984c6a08bd9d0cabb853412031454ede95739ad3e27c0667d90737dadbccaf',6068876,'cac642ab7873270052271210dbc1338bb1cb284cac84a4df4530e227170f298c'),(10,'FLMxRkymfg9ZN3YnCvB3d5eqf8uX7BX2UM','tokenroom','c1b024fc616ab858161873eebbf9f7a60dffa8e50d39d5e21b1e4f42c09bec5b',6068933,'e274f652b0c3d61a532cf8d67da48eb003927108e24ac263bdf86c670cd7e581'),(11,'FMf9WhZh69vfGS2cMbMs983HkVRaCKNJ5G','tokenroom','9085fc450a8aede0928a5b32dec8efcfd0d97a7c45e2083f7372082941936f20',6068984,'12401a5a6a1ec294aeea9cf5d6f5a14590660d3039314bf923494b796841d64e'),(12,'FU5LWMpzJ2DbdGpfgGNbqg8WwveWEN2C98','tokenroom','2f2d1cfb0dc3e3838d9cbfb70fd5d764908f8aaf60fd0e85fe08966d7eaa3c6b',6070104,'086a8ea6192f37f724fdffcfe554ec77afce1a2928c2c4b2a3898cfe596ddb59'),(13,'FHjeBLh6qkXugxBUsukUPMhm6oTTFNuBP6','tokenroom','bf77884ab97c094b647541c1946751d30d06e7d5ca4b3aa1a48135af5f9106d9',6071582,'1407766bb2cdfe3c55b2ca6dca9fd013e031fab1c4ae15a2d0a24aa6da105b52'),(14,'FLA7ZrH927Wq2VP1xn9VDaR3e4BfH1LbqA','tokenroom','53548a60cbc4aa288f4864336666ca5994dcc0820af9b5db570de943f91aeb7d',6071597,'cdfe60bc5f517a94f5eb6b5ac09cd1190238f1902bdab8e6bb82a42f68b30835'),(15,'FQy7ne8JT5t2ArtLAcJDaT71ZKWM9aiDLf','tokenroom','7e5f2e069fcac2934bd3768f4d00018deb222ccf77ebb349bb786322e3edc80b',6088593,'8299e866cc0d0308cbdf8a735e5707bde7cc154af363488b179ed03673b35634'),(16,'FRD9rwxUPSxYdp85du2rChapujWbc6xkHr','tokenroom','1de25adb450e6321fa020a4ebd0885c1d17e0e8e11439e3b9b502bcde1a8ce08',6090089,'e468647a9150c9e01039e4f0166a1c2e110c0d81d82802376059ebc28d2bf126'),(17,'FRBAN4f9ni8PpkarF643suoYMRS6jAY4bC','usd','31a21a8077b9a3c9bd4bb0f4842a337ef0c829a06b9e60d775ab011f81d992f4',6110389,'2f164d5daeb606920314778648c922747e1682697261117a07498e172bf9e07c'),(18,'es7CU9oZwHq4hk6QwnLY2HFp6MrBKE5bLo','usd','1d8e7a3589119da6071be81a2fa84d798c5e9f1cacc43cb0177b8fc6fd469485',6110392,'156a58cb29f173e36b74607f55d0188b74d40140e3474136a168d2920d7d599c'),(19,'FTVD8azQG9DRJ74JPverB8qrtw4NGQTXkJ','usd','e0f728ec71bef67d17c94d66e051ae4a611ec28454443337b268e5efc0a5f12f',6200710,'730b330b2ad132516d7da89a954f3f34a0340c6dfdffb1ea4d0d4c55306faa30'),(20,'FURSKiQnX4TXQvcQVw4khcmbrrxg9uuJrw','usd','a6e14a4314831c3f7b77f3e31aa0a9bdabb760ee477fe4690ff770401486c3ac',6227991,'159c8978cf50023b5b2589c327398ee44d5e28053f1c8cfe1c3d198b9f7a6c23'),(21,'FFS5hFXG7DBtdgzrLwixZLpenAmsCKRddm','usd','b8effaf0d97d88ba1d77c57ff6aade2561d46939bacbed2a6292612dec9f1b2d',6266394,'0cfaa4bae613bf2206d7e5eed21e9f478e67a545ab9348ca5d048710c971f4c8'),(22,'FPFeL5PXzW9bGosUjQYCxTHSMHidnygvvd','usd','7d7d3ba268da2336035bd78a6c2a4e78b5f6a34389803a4e6649c9290fabb49f',6266425,'8d297d6933259a3c7d25661bf0e76300136b0a6229f742514f210c7fc32f5f26'),(23,'FEVZLkn3RkKSvzshu1MbNY7j6pQCnNNh3n','usd','3e282174a586bf7daba45facf6f89185eeb2e24203975e5f2e3704e98e4c4324',6266452,'0a715de4e73d491205a891acd92d136c963e7a95c39ab7af079b3f8048b96d6a'),(24,'FBh7wCh8DhFBionnw3vy3JhjkSq8oNjXHH','usd','121eee17356a170ea59823f796254e14921d1ede091acef7b00e65cb8dfcf9c3',6334712,'f4888961d78ed840213971d000696910b559b424119b12c542e101934dd31ace');
/*!40000 ALTER TABLE `tokenAddressMapping` ENABLE KEYS */;
UNLOCK TABLES;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2025-01-05 20:53:45
