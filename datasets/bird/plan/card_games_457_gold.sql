{
  "relOp": "LogicalAggregate",
  "keys": [
    {
      "column": 0,
      "type": "VARCHAR"
    },
    {
      "column": 1,
      "type": "VARCHAR"
    }
  ],
  "aggs": [],
  "id": "3",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 64,
          "name": "$64",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 65,
          "name": "$65",
          "type": "VARCHAR"
        }
      ],
      "id": "2",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 48,
                "name": "$48",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "Molimo, Maro-Sorcerer",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          },
          "variableset": "[]",
          "id": "1",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "cards",
              "id": "0",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}