{
  "relOp": "LogicalAggregate",
  "keys": [
    {
      "column": 0,
      "type": "VARCHAR"
    }
  ],
  "aggs": [],
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 1,
          "name": "$1",
          "type": "VARCHAR"
        }
      ],
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "LIKE",
            "operator": "LIKE",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 75,
                "name": "$75",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "%DAS per1fekte Gegenmittel zu einer dichten Formation%",
                "type": "CHAR",
                "nullable": false,
                "precision": 54
              }
            ]
          },
          "variableset": "[]",
          "id": "3",
          "inputs": [
            {
              "relOp": "LogicalJoin",
              "joinType": "inner",
              "condition": {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 71,
                    "name": "$71",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 81,
                    "name": "$81",
                    "type": "VARCHAR"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "cards",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "foreign_data",
                  "id": "1",
                  "inputs": []
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}